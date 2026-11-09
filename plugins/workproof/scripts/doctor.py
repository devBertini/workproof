#!/usr/bin/env python
"""workproof-doctor: checks that this plugin's hooks and tools can run on this machine.

    workproof-doctor

Prints one line per check and what to do about a failed one: the Python that runs it (3.10 or later), the POSIX shell
the hooks start through, whether the hook launcher starts, the executable bits of the launcher and the bin/ tools
(macOS and Linux), and git. It then notes the module switches and the plans root as this process sees them (Claude
Code hands plugin options to hooks only, so a command run from the Bash tool sees the defaults unless a WORKPROOF_*
variable is set). It reads nothing but the plugin's own files and runs nothing but the launcher and `git --version`.
Exit 0 when every check passed, 1 otherwise. Python 3.10+ to pass, but it starts on any Python 3.
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WINDOWS = os.name == "nt"
MINIMUM = (3, 10)
MODULES = ("secret_guard", "proof_plans", "agent_roles", "flow_map", "long_runs", "delivery", "docker", "rust",
           "ui_conventions")
GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"


def find_shell(environ=None):
    """The POSIX shell hooks start through: `sh` elsewhere; on Windows Git Bash, never the WSL launcher in System32."""
    environ = os.environ if environ is None else environ
    if not WINDOWS:
        return shutil.which("sh")
    for path in (environ.get("CLAUDE_CODE_GIT_BASH_PATH"), shutil.which("bash"), GIT_BASH):
        if path and os.path.isfile(path) and "system32" not in path.lower():
            return path
    return None


def version(root):
    try:
        with open(os.path.join(root, ".claude-plugin", "plugin.json"), encoding="utf-8") as handle:
            return str(json.load(handle).get("version", "?"))
    except (OSError, ValueError):
        return "?"


def check_python(found=None):
    found = sys.version_info if found is None else found
    text = "Python %d.%d.%d at %s" % (found[0], found[1], found[2], sys.executable)
    if tuple(found[:2]) >= MINIMUM:
        return True, text, ""
    return False, text + " is too old", "install Python %d.%d or later and put it first on PATH" % MINIMUM


def check_shell(shell):
    if shell:
        return True, "POSIX shell for the hooks: " + shell, ""
    return (False, "no POSIX shell for the hooks",
            "install Git for Windows (its Git Bash runs the hooks) and restart Claude Code" if WINDOWS
            else "put `sh` on PATH")


def check_launcher(root, shell):
    launcher = os.path.join(root, "hooks", "run-hook.cmd")
    if not os.path.isfile(launcher):
        return False, "the hook launcher is missing: " + launcher, "reinstall the plugin"
    if not shell:
        return False, "the hook launcher was not tried (no shell)", "fix the shell first"
    line = '"%s" modules.py --self-test' % launcher.replace("\\", "/")
    try:
        out = subprocess.run([shell, "-c", line], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, "the hook launcher did not finish: %s" % error, "run this command again; if it repeats, reinstall"
    if out.returncode == 0:
        return True, "the hook launcher starts: " + (out.stdout.strip() or "ok"), ""
    detail = (out.stderr.strip() or out.stdout.strip() or "no output").splitlines()[-1]
    fix = "install Python 3.10 or later (python3, python or py -3 on PATH), or reinstall the plugin if Python is fine"
    if "ermission denied" in detail:
        fix = 'restore the executable bit: chmod +x "%s"' % launcher
    return False, "the hook launcher fails (exit %d): %s" % (out.returncode, detail), fix


def check_modes(root):
    """macOS and Linux run the launcher and the bin/ tools directly: each needs its executable bit."""
    if WINDOWS:
        return True, "executable bits: not used on Windows", ""
    paths = [os.path.join(root, "hooks", "run-hook.cmd")]
    tools = os.path.join(root, "bin")
    paths += [os.path.join(tools, name) for name in sorted(os.listdir(tools))] if os.path.isdir(tools) else []
    lost = [p for p in paths if os.path.isfile(p) and not os.access(p, os.X_OK)]
    if not lost:
        return True, "the launcher and %d bin tools are executable" % (len(paths) - 1), ""
    return False, "not executable: " + ", ".join(os.path.relpath(p, root) for p in lost), \
        "chmod +x " + " ".join('"%s"' % p for p in lost)


def check_git():
    path = shutil.which("git")
    if not path:
        return False, "git not found", "install git (guarded-commit, stage-close compaction and check-flows --changed use it)"
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, "git does not run: %s" % error, "reinstall git"
    return out.returncode == 0, (out.stdout.strip() or "git") + " at " + path, "" if out.returncode == 0 else "reinstall git"


def notes(environ):
    off = {"false", "0", "no", "off"}
    seen = []
    for key in MODULES:
        value = environ.get("CLAUDE_PLUGIN_OPTION_" + key.upper())
        state = "default (on)" if value is None else ("off" if value.strip().lower() in off else "on")
        seen.append("%s %s" % (key, state))
    yield "modules as this process sees them (plugin options reach hooks only): " + ", ".join(seen)
    for name in ("WORKPROOF_PLANS_ROOT", "PROOF_PLANS_ROOT", "CLAUDE_PLUGIN_OPTION_PLANS_ROOT"):
        value = (environ.get(name) or "").strip()
        if value:
            exists = os.path.isdir(os.path.expanduser(value))
            yield "plans root: %s (from %s)%s" % (value, name, "" if exists else "; not a folder, so it counts as unset")
            return
    yield "plans root: none set"


def run(root=None, environ=None, out=None):
    out = sys.stdout if out is None else out
    root = os.path.abspath(root or os.path.dirname(HERE))
    environ = os.environ if environ is None else environ
    shell = find_shell(environ)
    checks = [check_python(), check_shell(shell), check_launcher(root, shell), check_modes(root), check_git()]
    print("workproof-doctor: plugin %s at %s" % (version(root), root), file=out)
    for ok, what, fix in checks:
        print("%s  %s" % ("ok  " if ok else "FAIL", what), file=out)
        if not ok and fix:
            print("      fix: " + fix, file=out)
    for line in notes(environ):
        print("note  " + line, file=out)
    failed = sum(1 for ok, _, _ in checks if not ok)
    print("workproof-doctor: " + ("every check passed" if not failed else "%d check(s) failed" % failed), file=out)
    return 1 if failed else 0


def main(argv):
    root = argv[argv.index("--plugin") + 1] if "--plugin" in argv[:-1] else None  # for the tests
    return run(root)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
