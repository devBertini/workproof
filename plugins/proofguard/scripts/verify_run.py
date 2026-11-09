#!/usr/bin/env python
"""A command run that leaves evidence: exit code, duration, the tail of the output and the SHA-256 of all of it.

  verify-run run [--cwd <dir>] [--timeout N] [--shell] -- <command...>
  verify-run check <json | file | ->

`run` runs the command (no shell unless --shell, then the words are joined into one shell line) from `--cwd`, bounded
by `--timeout` seconds (default 300; on expiry the whole process tree is killed), and prints ONE JSON object:
command, shell, cwd, exit, timed_out, duration_s, output_bytes, sha256 (of stdout and stderr together, in the order
written), tail (the last 40 lines), time. Exit status: 0 the command passed, 1 it failed or timed out, 2 the run was
refused or misused (the reason is on stderr and in a JSON `refused` field on stdout).

A pipeline fails when any of its stages fails (`set -o pipefail`): `--shell` runs the line under bash with it, and
`bash -c`, `zsh -c` and `sh -c` (run under bash) get it in front of their script. A bare `|` word without --shell is
refused, since nothing would run it as a pipeline. On Windows the bash used is Git Bash (the path in
CLAUDE_CODE_GIT_BASH_PATH, else the first bash on PATH outside System32, else Git's usual install folder), never the
WSL launcher.

Refused, never run: any command that carries `--execute`, `git push` (git, then push anywhere later in the command
line, so a wrapper whose name ends in `-git` is covered), and `rm` with a recursive flag. The check is on the words of
the command line, wherever they sit (`sh -c '...'` included), so it is deliberately conservative: `git log --grep push`
is refused too. It is a guard against outward and destructive actions that need a person's approval, not a sandbox.

`check` reads such an object (JSON text, a file path or `-` for stdin) and validates its fields and its internal
consistency (an empty output has the SHA-256 of nothing, and only it). Exit 0 valid and the command passed, 1 valid but
the command failed or timed out, 2 malformed.
"""
import argparse
import collections
import datetime
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

WINDOWS = os.name == "nt"
GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"


def find_bash():
    """The bash to start: on Windows Git Bash (never System32's WSL launcher), elsewhere the one on PATH."""
    if not WINDOWS:
        return shutil.which("bash") or "bash"
    for path in (os.environ.get("CLAUDE_CODE_GIT_BASH_PATH"), shutil.which("bash"), GIT_BASH):
        if path and os.path.isfile(path) and "system32" not in path.lower():
            return path
    return "bash"


BASH = find_bash()
TAIL_LINES = 40
DEFAULT_TIMEOUT = 300
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
PIPEFAIL = "set -o pipefail; "
TIMEOUT_EXIT = 124
NOT_FOUND_EXIT = 127

RM_RECURSIVE = re.compile(r"(?<![\w.-])rm\b[^;&|\n]*\s-(?:[A-Za-z]*[rR]|-recursive\b)")
GIT_PUSH = re.compile(r"\bgit\b.*\bpush\b")  # also matches `my-git push`: `git` is a word after the hyphen


class Refused(Exception):
    """The command is one this tool never runs; the message says why."""


def refusal(words):
    """The reason a command line is refused, or None. `words` is a list of strings (argv or the shell line's words)."""
    text = " ".join(words)
    if "--execute" in text:
        return "carries --execute (an outward write: get approval first)"
    if GIT_PUSH.search(text):
        return "is a git push (get approval first)"
    if RM_RECURSIVE.search(text):
        return "is a recursive rm (get approval first)"
    return None


def launch_plan(words, shell):
    """How the command is started: (argv, shell flag, executable). A pipeline must fail when any stage fails, or
    `build | tail -40` would pass on red: a shell line runs under bash with pipefail; `bash -c`, `zsh -c` and `sh -c`
    get `set -o pipefail;` in front of their script, and `bash` and `sh` run under the bash found above."""
    if shell:
        return [BASH, "-c", PIPEFAIL + " ".join(words)], False, None
    flags = words[1] if len(words) > 2 else ""
    if os.path.basename(words[0]) in ("bash", "zsh", "sh") and re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", flags):
        argv = list(words)
        argv[2] = PIPEFAIL + argv[2]
        if os.path.basename(argv[0]) in ("bash", "sh"):
            argv[0] = BASH
        return argv, False, None
    return list(words), False, None


def display(words, shell):
    return " ".join(words) if shell else shlex.join(words)


def kill_tree(proc):
    """Stop the process and everything it started."""
    if WINDOWS:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()


def run_command(words, cwd=".", timeout=DEFAULT_TIMEOUT, shell=False):
    """Run the command and return the evidence object. Raises Refused for an outward or destructive command and
    ValueError for a bad folder, timeout or empty command."""
    if not words:
        raise ValueError("no command given")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError("--timeout must be a positive number of seconds")
    reason = refusal(words)
    if reason:
        raise Refused(f"{display(words, shell)}: {reason}")
    if not shell and "|" in words:
        raise Refused(f"{display(words, shell)}: a bare | word needs a shell to be a pipeline and none runs without "
                      "--shell; use --shell, or bash -c '<pipeline>'")
    folder = os.path.abspath(cwd)
    if not os.path.isdir(folder):
        raise ValueError(f"--cwd {cwd} is not a folder")
    argv, use_shell, executable = launch_plan(words, shell)
    started = time.monotonic()
    timed_out = False
    with tempfile.TemporaryFile() as out:
        try:
            proc = subprocess.Popen(argv, cwd=folder, shell=use_shell, executable=executable, stdin=subprocess.DEVNULL,
                                    stdout=out, stderr=subprocess.STDOUT, start_new_session=not WINDOWS)
        except OSError as error:
            out.write(f"verify_run: cannot run {argv[0]}: {error}\n".encode())
            code = NOT_FOUND_EXIT
        else:
            try:
                code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                kill_tree(proc)
                proc.wait()
                code = TIMEOUT_EXIT
        duration = time.monotonic() - started
        out.seek(0)
        digest, size, tail = hashlib.sha256(), 0, collections.deque(maxlen=TAIL_LINES + 1)
        partial = b""
        while True:
            chunk = out.read(1 << 16)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            pieces = (partial + chunk).split(b"\n")
            partial = pieces.pop()
            tail.extend(pieces)
        if partial:
            tail.append(partial)
    lines = [line.decode("utf-8", "replace").rstrip("\r") for line in tail][-TAIL_LINES:]
    return {"command": display(words, shell), "shell": bool(shell), "cwd": folder, "exit": code,
            "timed_out": timed_out, "duration_s": round(duration, 3), "output_bytes": size,
            "sha256": digest.hexdigest(), "tail": lines,
            "time": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}


def passed(result):
    return result.get("exit") == 0 and result.get("timed_out") is False


def validate(obj):
    """The list of problems in an evidence object; empty when it is well formed and consistent."""
    if not isinstance(obj, dict):
        return ["not a JSON object"]
    problems = []

    def need(key, kind, ok=lambda v: True, what=""):
        value = obj.get(key)
        wrong = key not in obj or isinstance(value, bool) != (kind is bool) or not isinstance(value, kind) or not ok(value)
        if wrong:
            problems.append(f"{key}: missing or not {what or kind.__name__}")
        return not wrong

    need("command", str, lambda v: bool(v.strip()), "a non-empty string")
    need("shell", bool)
    need("cwd", str, lambda v: bool(v), "a non-empty string")
    need("exit", int, what="an integer")
    need("timed_out", bool)
    need("duration_s", (int, float), lambda v: v >= 0, "a number of seconds, 0 or more")
    size_ok = need("output_bytes", int, lambda v: v >= 0, "an integer, 0 or more")
    sha_ok = need("sha256", str, lambda v: re.fullmatch(r"[0-9a-f]{64}", v) is not None, "64 lowercase hex characters")
    need("tail", list, lambda v: len(v) <= TAIL_LINES and all(isinstance(x, str) for x in v),
         f"a list of at most {TAIL_LINES} strings")
    need("time", str, lambda v: bool(v), "a timestamp string")
    if "refused" in obj:
        problems.append("refused: this is a refusal, not a run")
    if sha_ok and size_ok and (obj["output_bytes"] == 0) != (obj["sha256"] == EMPTY_SHA256):
        problems.append("sha256 and output_bytes disagree about whether the output was empty")
    if obj.get("timed_out") is True and obj.get("exit") == 0:
        problems.append("timed_out with exit 0")
    if obj.get("output_bytes") == 0 and obj.get("tail"):
        problems.append("tail has lines but output_bytes is 0")
    return problems


def split_command(argv):
    """(options, command words) around the first `--`."""
    if "--" in argv:
        i = argv.index("--")
        return argv[:i], argv[i + 1:]
    return argv, []


def main(argv=None):
    options, words = split_command(sys.argv[1:] if argv is None else list(argv))
    ap = argparse.ArgumentParser(prog="verify-run")
    sub = ap.add_subparsers(dest="mode", required=True)
    r = sub.add_parser("run")
    r.add_argument("--cwd", default=".")
    r.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    r.add_argument("--shell", action="store_true")
    c = sub.add_parser("check")
    c.add_argument("source")
    a = ap.parse_args(options)

    if a.mode == "run":
        try:
            result = run_command(words, a.cwd, a.timeout, a.shell)
        except Refused as error:
            print(json.dumps({"refused": str(error)}))
            sys.stderr.write(f"verify_run: refused: {error}\n")
            return 2
        except ValueError as error:
            sys.stderr.write(f"verify_run: {error}\n")
            return 2
        print(json.dumps(result, ensure_ascii=False))
        return 0 if passed(result) else 1

    if words:
        sys.stderr.write("verify_run: check takes no command\n")
        return 2
    try:
        if a.source == "-":
            text = sys.stdin.read()
        elif a.source.lstrip().startswith("{"):
            text = a.source
        else:
            with open(a.source, encoding="utf-8") as f:
                text = f.read()
        obj = json.loads(text)
    except (OSError, ValueError) as error:
        sys.stderr.write(f"verify_run: cannot read the object: {error}\n")
        return 2
    problems = validate(obj)
    if problems:
        sys.stderr.write("verify_run: invalid: " + "; ".join(problems) + "\n")
        return 2
    if passed(obj):
        print(f"valid: {obj['command']} exit 0 in {obj['duration_s']} s")
        return 0
    print(f"valid but failed: {obj['command']} exit {obj['exit']}{' (timed out)' if obj['timed_out'] else ''}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
