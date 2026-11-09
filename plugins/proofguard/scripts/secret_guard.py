"""PreToolUse hook for the Bash and PowerShell tools: blocks a command that would read or print a secret.

Three rule sets run in one process:
  - secret files: a command that is given the name of a secret file (see secret_patterns.SECRET_NAMES, plus the
    user's `extra_secret_names` option) is blocked, unless it only lists, tests, hashes, creates, removes or writes
    it, hands it to a tool that uses it without printing it (`ssh -i`, `--env-file`), or names it as a search
    pattern or a commit message;
  - environment dumps: `env`, `printenv`, `export -p`, `Get-ChildItem env:`, `/proc/*/environ`, and printing a
    variable whose name says it holds a secret (`echo $API_TOKEN`);
  - Docker leaks: see docker_rules.py.
Interpreter code (`python -c`, a heredoc fed to `node`, `bash -c` and so on) is searched for the names of secret
files and, for shells, checked again as a command of its own.

Claude Code's file-read deny rules cover its file tools and the commands it recognizes as reads; they miss other
programs, such as an interpreter that opens the file itself. This hook covers those. It is a guard against mistakes,
not a sandbox: a command built to hide a file name gets past it.

Input: the hook JSON on stdin. Exit 0 lets the command run; exit 2 blocks it, with the reason on stderr for Claude.
Python 3.10+, standard library only.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from docker_rules import docker_verdict  # noqa: E402
from secret_patterns import compile_names, extra_names_from_env  # noqa: E402
from modules import module_enabled  # noqa: E402
from shell_words import pipeline, program_of, segments, tokens  # noqa: E402

# Programs (lower case) that never print a file's content: they list, test, hash, count, create, remove or change
# the mode of a file, or print its name.
NO_READ = {"ls", "dir", "stat", "test", "[", "[[", "chmod", "chown", "file", "echo", "printf", "wc", "du",
           "md5sum", "sha1sum", "sha256sum", "sha512sum", "touch", "rm", "rmdir", "unlink", "mkdir",
           "test-path", "get-childitem", "gci", "get-item", "gi", "split-path", "resolve-path", "get-filehash",
           "remove-item", "ri", "del", "erase", "new-item", "ni"}
COPIERS = {"cp", "copy-item", "cpi", "copy"}
MOVERS = {"mv", "move-item", "mi", "move", "ren", "rename-item"}
GREPS = {"grep", "egrep", "fgrep", "rg"}
GREP_VALUED = {"-A", "-B", "-C", "-m", "--max-count", "-g", "--glob", "-t", "--type"}
KEY_USERS = {"ssh", "scp", "sftp"}
GIT_NAME_ONLY = {"check-ignore", "status", "ls-files"}
FIND_RUNS = {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprintf", "-fls"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "powershell", "pwsh", "cmd"}
BACKSLASH_SHELLS = {"powershell", "pwsh", "cmd"}
INTERPRETERS = SHELLS | {"node", "ruby", "perl", "php", "swift", "deno", "bun"}
CODE_FLAGS = {"-c", "-e", "-E", "-Command", "/c"}
PIPE_READERS = {"xargs", "cat", "get-content", "gc", "type", "select-string", "sls", "more", "less", "head", "tail"}
REDIRECT = re.compile(r"^[0-9&]?>>?\|?$")
LITERAL = re.compile(r"'([^'\n]*)'|\"([^\"\n]*)\"")
HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")
SUBSTITUTION = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
GIT_VALUED = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
WEB_ADDRESS = re.compile(r"^https?://", re.IGNORECASE)
GLOB_CHARS = re.compile(r"[*?\[{]")
# Environment variables whose name says they hold a secret, and the ways a command refers to one.
SECRET_VARIABLE = re.compile(r"TOKEN|SECRET|PASSW|API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|CREDENTIAL|(^|_)PAT$|(^|_)AUTH($|_)",
                             re.IGNORECASE)
VARIABLE_REFERENCE = re.compile(r"\$(?:env:)?\{?([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
PRINTERS = {"echo", "printf", "print", "write-host", "write-output", "echo.", "say"}
DUMPERS = {"export", "declare", "typeset", "set"}
ENVIRONMENT_FILE = re.compile(r"/proc/[^/\s]+/environ")
ENVIRONMENT_API = re.compile(r"\[(System\.)?Environment\]::GetEnvironmentVariables?\s*\(\s*(['\"]?([A-Za-z0-9_]*))?",
                             re.IGNORECASE)


def is_interpreter(program):
    return program in INTERPRETERS or re.fullmatch(r"python[0-9.]*", program) is not None


def secret_kind(word, names):
    """What secret file the word names, or None.

    A word is also tested with `\\` read as `/` (a Windows path), by its parts around `=`, `:` and `@`, and, when it
    is a glob, by the part before the first glob character (`.env*`). A web address names a remote file, not a
    local one, so it is never a secret file here."""
    word = word.strip()
    if WEB_ADDRESS.match(word):
        return None
    candidates = [word]
    if "\\" in word:
        candidates.append(word.replace("\\", "/"))
    for sep in ("=", ":", "@"):
        candidates += [part for c in list(candidates) if sep in c for part in c.split(sep)]
    for c in list(candidates):
        glob = GLOB_CHARS.search(c)
        if glob and glob.start() > 0:
            candidates.append(c[:glob.start()])
    for candidate in candidates:
        for pattern, what in names:
            if candidate and pattern.search(candidate):
                return what
    return None


def literal_name_only(literal, piece):
    """True when the literal reads as a command line in which a program that does not read holds the piece."""
    piece = piece.strip("'\"")
    for segment in segments(tokens(literal)):
        program, args = program_of(segment)
        if program and piece in args and harmless(program, args, args.index(piece), [])[0]:
            return True
    return False


def code_secret(code, names):
    """(name, kind) of a secret file that a string literal in interpreter code names, whole or by a word in it."""
    for match in LITERAL.finditer(code):
        literal = match.group(1) if match.group(1) is not None else match.group(2)
        for piece in [literal] + literal.split():
            kind = secret_kind(piece, names)
            if kind and not (piece != literal and literal_name_only(literal, piece)):
                return piece, kind
    return None


def split_heredocs(command):
    """The command without its heredoc bodies, and each body as (index of the line that opened it, body, quoted)."""
    kept = []
    bodies = []
    waiting = []
    body = []
    for line in command.split("\n"):
        if waiting:
            delimiter, strip_tabs, opener, quoted = waiting[0]
            if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                bodies.append((opener, "\n".join(body), quoted))
                body = []
                waiting.pop(0)
            else:
                body.append(line)
            continue
        kept.append(line)
        for match in HEREDOC.finditer(line):
            waiting.append((match.group(3), match.group(1) == "-", len(kept) - 1, bool(match.group(2))))
    if waiting:
        bodies.append((waiting[0][2], "\n".join(body), waiting[0][3]))
    return "\n".join(kept), bodies


def git_subcommand(args):
    i = 0
    while i < len(args):
        if args[i] in GIT_VALUED:
            i += 2
        elif args[i].startswith("-"):
            i += 1
        else:
            return args[i]
    return ""


def grep_pattern_index(args):
    """Index of the word grep reads as its pattern (the first operand), or None when -e or -f gives the pattern."""
    i = 0
    while i < len(args):
        word = args[i]
        if word in ("-e", "-f", "--regexp", "--file") or word.startswith(("--regexp=", "--file=")):
            return None
        if word == "--":
            return i + 1 if i + 1 < len(args) else None
        if word in GREP_VALUED:
            i += 2
        elif word.startswith("-") and len(word) > 1:
            i += 1
        else:
            return i
    return None


def harmless(program, args, i, names):
    """(True, the program reads nothing) when args[i], a secret file name, is used in a way that prints nothing of it."""
    word, low = args[i], program.lower()
    before = args[i - 1] if i > 0 else ""
    if low in NO_READ:
        return True, True
    if REDIRECT.match(before) or before in ("-out", "-o", "--output"):  # `> .env`, `-out key.pem`: written, not read
        return True, False
    if word.startswith("--env-file=") or before == "--env-file":  # loaded into a process's environment
        return True, False
    if low in KEY_USERS and before == "-i":
        return True, False
    if low == "git":
        sub = git_subcommand(args)
        if sub in GIT_NAME_ONLY or (sub == "rm" and "--cached" in args):
            return True, True
        return before in ("-m", "--message") and sub in ("commit", "tag", "merge", "stash"), False
    if low == "find":
        return not FIND_RUNS.intersection(args), True
    if low in GREPS:
        if word.startswith(("--include=", "--exclude=", "--exclude-dir=", "--glob=")) or before in ("-g", "--glob"):
            return True, False
        return grep_pattern_index(args) == i, False
    if low in ("select-string", "sls"):
        return before.lower() == "-pattern" or (not before.startswith("-") and i == 0), False
    if low in COPIERS:
        return copy_destination(args, i, names), False
    if low in MOVERS:
        operands = [a for a in args if not a.startswith("-")]
        return all(secret_kind(a, names) for a in operands), False
    return False, False


def copy_destination(args, i, names):
    """True when a copy only writes the word: it is the last argument and no other argument names a secret file.

    A copy from a template into a secret file creates it and prints nothing; copying a secret file out still blocks."""
    if i != len(args) - 1 or i == 0:
        return False
    if any(a.lower() in ("-t", "--target-directory", "-destination") or a.startswith("--target-directory=")
           for a in args):
        return False
    return not any(secret_kind(a, names) for a in args[:i] if not a.startswith("-"))


def environment_dump(segment, program, args):
    """The reason the simple command prints the environment or a secret variable, or None."""
    low = program.lower()
    if not program and "env" in segment:
        return "bare `env` prints every environment variable"
    if low == "printenv":
        if not args:
            return "bare `printenv` prints every environment variable"
        secret = next((a for a in args if SECRET_VARIABLE.search(a)), None)
        if secret:
            return f"`printenv {secret}` prints a secret variable"
    if low in DUMPERS and all(a in ("-p", "-x", "-px", "-xp") for a in args):
        return f"`{program}` without a variable name prints every variable"
    if low in ("get-childitem", "gci", "dir", "ls", "get-item", "gi"):
        for a in args:
            if a.lower().startswith("env:") and (a[4:] in ("", "/", "*") or SECRET_VARIABLE.search(a[4:])):
                return "listing `env:` prints environment variables"
    if any(ENVIRONMENT_FILE.search(w) for w in [program] + args):
        return "`/proc/*/environ` holds a process's environment"
    if low in PRINTERS or (program.lower().startswith("$env:") and not args):
        for word in ([program] if low not in PRINTERS else args):
            for name in VARIABLE_REFERENCE.findall(word):
                if SECRET_VARIABLE.search(name):
                    return f"printing `${name}` shows a secret variable"
    return None


def nested_code(program, args, i):
    """The program that runs args[i] as code (`bash -c '<code>'`, also behind docker exec, xargs or sudo), or None."""
    if i == 0 or args[i - 1] not in CODE_FLAGS:
        return None
    if is_interpreter(program):
        return program
    if i >= 2:
        runner = os.path.basename(args[i - 2].replace("\\", "/")).lower()
        runner = runner[:-4] if runner.endswith(".exe") else runner
        if is_interpreter(runner):
            return runner
    return None


def check_code(runner, code, names, depth):
    if runner in SHELLS:
        return verdict(code.replace("\\", "/") if runner in BACKSLASH_SHELLS else code, names, depth + 1)
    hit = code_secret(code, names)
    return f"`{runner}` code names the secret file `{hit[0]}` ({hit[1]})" if hit else None


def verdict(command, names=None, depth=0):
    """None when the command may run, or the reason it is blocked. `names` comes from compile_names."""
    if depth > 3:
        return None
    if names is None:
        names = compile_names()
    api = ENVIRONMENT_API.search(command)
    if api and (api.group(0).lower().find("variables") >= 0 or SECRET_VARIABLE.search(api.group(3) or "")):
        return "`[Environment]::GetEnvironmentVariable` here prints environment variables"
    text, bodies = split_heredocs(command.replace("\\\n", " "))
    lines = text.split("\n")
    for opener, body, quoted in bodies:
        if not quoted:  # the shell expands `$(...)` in an unquoted heredoc body before the program reads it
            text += "\n" + " ".join(m.group(0) for m in SUBSTITUTION.finditer(body))
    for match in SUBSTITUTION.finditer(text):
        found = verdict(match.group(1) if match.group(1) is not None else match.group(2), names, depth + 1)
        if found:
            return found
    feeds_reader = False  # a no-read command named a secret file and pipes into the next one
    for segment, operator in pipeline(tokens(text)):
        program, args = program_of(segment)
        if feeds_reader and (program.lower() in PIPE_READERS or is_interpreter(program)):
            return f"a secret file name is piped into `{program}`, which reads it"
        feeds_reader = False
        if not program and "env" not in segment:
            continue
        found = docker_verdict(program, args) or environment_dump(segment, program, args)
        if found:
            return found
        kind = secret_kind(program, names) if program else None
        if kind and not program.startswith("$"):
            return f"the secret file `{program}` ({kind}) is run or read as a command"
        for i, word in enumerate(args):
            runner = nested_code(program, args, i)
            if runner:
                found = check_code(runner, word, names, depth)
                if found:
                    return found
                continue
            kind = secret_kind(word, names)
            if not kind:
                continue
            allowed, no_read = harmless(program, args, i, names)
            if not allowed:
                return f"`{program}` is given the secret file `{word}` ({kind})"
            if no_read and operator in ("|", "|&"):
                feeds_reader = True
    for opener, body, quoted in bodies:
        owner = segments(tokens(lines[opener]))
        program = next((program_of(s)[0] for s in owner if "<<" in s or any(w.startswith("<<") for w in s)), "")
        if is_interpreter(program):
            found = check_code(program, body, names, depth)
            if found:
                return found
    return None


def main():
    if not module_enabled("secret_guard"):
        return 0
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(data, dict):
        return 0
    tool_input = data.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if data.get("tool_name") not in ("Bash", "PowerShell") or not isinstance(command, str):
        return 0
    names = compile_names(extra_names_from_env())
    # A backslash is PowerShell's path separator; the name patterns and the tokenizer read `/`.
    reason = verdict(command.replace("\\", "/") if data["tool_name"] == "PowerShell" else command, names)
    if reason is None:
        return 0
    print(f"Blocked by proofguard (secret-guard module): {reason}. Secrets stay out of the conversation: refer to "
          f"them by path and let the tool that needs them read them. Listing, testing, creating or removing a file "
          f"is allowed. If this is a false alarm, ask the user instead of working around the guard.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
