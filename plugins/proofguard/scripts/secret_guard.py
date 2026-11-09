"""PreToolUse hook for the Bash and PowerShell tools: blocks a command that would read or print a secret.

Two rule sets run in one process:
  - secret files: a command that is given the name of a secret file (see secret_patterns.SECRET_NAMES, plus the
    user's `extra_secret_names` option) is blocked, unless it only lists it, tests it, changes its mode, prints its
    name or hands it to Docker as an env file;
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
from shell_words import program_of, segments, tokens  # noqa: E402

# Programs that only list, test, describe or print the name of a file, or change its mode.
NAME_ONLY = {"ls", "stat", "test", "[", "[[", "chmod", "chown", "file", "echo", "printf",
             "Test-Path", "Get-ChildItem", "Get-Item", "Split-Path", "Resolve-Path"}
GIT_NAME_ONLY = {"check-ignore", "status", "ls-files"}
FIND_RUNS = {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprintf", "-fls"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "powershell", "pwsh", "cmd"}
INTERPRETERS = SHELLS | {"node", "ruby", "perl", "php", "swift", "deno", "bun"}
CODE_FLAGS = {"-c", "-e", "-E", "-Command", "/c"}
LITERAL = re.compile(r"'([^'\n]*)'|\"([^\"\n]*)\"")
HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")
SUBSTITUTION = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
GIT_VALUED = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
WEB_ADDRESS = re.compile(r"^https?://", re.IGNORECASE)


def is_interpreter(program):
    return program in INTERPRETERS or re.fullmatch(r"python[0-9.]*", program) is not None


def secret_kind(word, names):
    """What secret file the word names, or None. A word with `=` or `:` is also tested by its parts.

    A web address names a remote file, not a local one, so it is never a secret file here."""
    if WEB_ADDRESS.match(word.strip()):
        return None
    candidates = [word.strip()]
    for sep in ("=", ":", "@"):
        candidates += [part for c in list(candidates) if sep in c for part in c.split(sep)]
    for candidate in candidates:
        for pattern, what in names:
            if candidate and pattern.search(candidate):
                return what
    return None


def literal_name_only(literal, piece):
    """True when the literal reads as a command line in which a name-only program holds the piece (ls, test, ...)."""
    piece = piece.strip("'\"")
    for segment in segments(tokens(literal)):
        program, args = program_of(segment)
        if program and piece in args and name_only_use(program, args, piece):
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
    """The command without its heredoc bodies, and each body with the index of the line that opened it."""
    kept = []
    bodies = []
    waiting = []
    body = []
    for line in command.split("\n"):
        if waiting:
            delimiter, strip_tabs, opener = waiting[0]
            if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                bodies.append((opener, "\n".join(body)))
                body = []
                waiting.pop(0)
            else:
                body.append(line)
            continue
        kept.append(line)
        for match in HEREDOC.finditer(line):
            waiting.append((match.group(3), match.group(1) == "-", len(kept) - 1))
    if waiting:
        bodies.append((waiting[0][2], "\n".join(body)))
    return "\n".join(kept), bodies


def name_only_use(program, args, word):
    if program in NAME_ONLY:
        return True
    if program == "git":
        sub, i = "", 0
        while i < len(args):
            if args[i] in GIT_VALUED:
                i += 2
            elif args[i].startswith("-"):
                i += 1
            else:
                sub = args[i]
                break
        return sub in GIT_NAME_ONLY or (sub == "rm" and "--cached" in args)
    if program == "find":
        return not FIND_RUNS.intersection(args)
    if program in ("docker", "docker-compose"):
        return word.startswith("--env-file=") \
            or any(args[i] == "--env-file" and args[i + 1] == word for i in range(len(args) - 1))
    return False


def copy_destination(program, args, i, names):
    """True when `cp` only writes the word: it is the last argument and no other argument names a secret file.

    A copy from a template into a secret file creates it and prints nothing; copying a secret file out still blocks."""
    if program != "cp" or i != len(args) - 1 or i == 0:
        return False
    if any(a in ("-t", "--target-directory") or a.startswith("--target-directory=") for a in args):
        return False
    return not any(secret_kind(a, names) for a in args[:i] if not a.startswith("-"))


def verdict(command, names=None, depth=0):
    """None when the command may run, or the reason it is blocked. `names` comes from compile_names."""
    if depth > 3:
        return None
    if names is None:
        names = compile_names()
    text, bodies = split_heredocs(command.replace("\\\n", " "))
    for match in SUBSTITUTION.finditer(text):
        found = verdict(match.group(1) if match.group(1) is not None else match.group(2), names, depth + 1)
        if found:
            return found
    lines = text.split("\n")
    for segment in segments(tokens(text)):
        program, args = program_of(segment)
        if not program:
            continue
        found = docker_verdict(program, args)
        if found:
            return found
        for i, word in enumerate(args):
            if is_interpreter(program) and i > 0 and args[i - 1] in CODE_FLAGS:
                found = verdict(word, names, depth + 1) if program in SHELLS else None
                if found:
                    return found
                hit = None if program in SHELLS else code_secret(word, names)
                if hit:
                    return f"`{program}` code names the secret file `{hit[0]}` ({hit[1]})"
                continue
            kind = secret_kind(word, names)
            if kind and not name_only_use(program, args, word) and not copy_destination(program, args, i, names):
                return f"`{program}` is given the secret file `{word}` ({kind})"
    for opener, body in bodies:
        owner = segments(tokens(lines[opener]))
        program = next((program_of(s)[0] for s in owner if "<<" in s or any(w.startswith("<<") for w in s)), "")
        if program in SHELLS:
            found = verdict(body, names, depth + 1)
            if found:
                return found
        elif is_interpreter(program):
            hit = code_secret(body, names)
            if hit:
                return f"`{program}` code names the secret file `{hit[0]}` ({hit[1]})"
    return None


def main():
    if not module_enabled("secret_guard"):
        return 0
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    command = (data.get("tool_input") or {}).get("command") if isinstance(data, dict) else None
    tool = data.get("tool_name") if isinstance(data, dict) else None
    if tool not in ("Bash", "PowerShell") or not isinstance(command, str):
        return 0
    names = compile_names(extra_names_from_env())
    # A backslash is PowerShell's path separator; the name patterns and the tokenizer read `/`.
    reason = verdict(command.replace("\\", "/") if tool == "PowerShell" else command, names)
    if reason is None:
        return 0
    print(f"Blocked by proofguard (secret-guard module): {reason}. Secrets stay out of the conversation: refer to them by path and let "
          f"the tool that needs them read them. Listing or testing a file (ls, test, stat, chmod) is allowed. If this "
          f"is a false alarm, ask the user instead of working around the guard.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
