#!/usr/bin/env python
"""The one gate of this repository: every suite and every static check, side by side.

    python -B scripts/check_all.py [--clean-clone] [--require-claude] [--list]

Runs, on every core:
  - every Python suite (`test_*.py` under scripts/, tests/ and plugins/*/scripts/), one process each;
  - `claude plugin test`, `claude plugin validate --strict --profile directory` for each plugin and
    `claude plugin validate --strict` for the marketplace (skipped with a notice when no `claude` is found, unless
    --require-claude);
  - the leak check (with the private patterns file when it exists);
  - static checks of the package: file modes in the git index, strict YAML front matter, file sizes, file kinds,
    file count, the README's coverage of options and commands, and the cross-references of skills.

--clean-clone clones HEAD into a temporary folder and runs the gate there, so nothing leans on an untracked file.
Exit 0 when everything passed, 1 otherwise. Python 3.10+, standard library only.
"""
import argparse
import concurrent.futures
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_FILE_BYTES = 256 * 1024
MAX_FILES = 512
TEXT_SUFFIXES = {".md", ".json", ".py", ".ts", ".tsx", ".cmd", ".sh", ".txt", ".toml", ".yml", ".yaml", ".svg", ""}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
EXECUTABLE = ("scripts/run-hook.cmd", "plugins/*/hooks/*.cmd", "plugins/*/bin/*")
# Hyphenated words that open a code span in a document and are not commands of the plugin: the cross-reference check
# takes any other such word for a bin/ command, so a renamed or missing tool is caught.
NOT_COMMANDS = {"general-purpose", "no-new-privileges", "opt-level", "swift-test", "reviewing-migrations",
                "running-benchmarks"}
PLAIN_SCALAR_TRAPS = (": ", " #")
RESERVED_STARTS = tuple("[{&*!|>%@`\"'")


def git(root, *args):
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, check=True).stdout


def tracked(root, *patterns):
    """The tracked files that match, without those deleted in the working tree and not committed yet."""
    return [line for line in git(root, "ls-files", "--", *patterns).splitlines()
            if line and os.path.isfile(os.path.join(root, line))]


def plugins(root):
    return sorted(os.path.dirname(os.path.dirname(p)).replace("\\", "/")
                  for p in glob.glob(os.path.join(root, "plugins", "*", ".claude-plugin", "plugin.json")))


def find_claude():
    for candidate in (shutil.which("claude"), os.environ.get("CLAUDE_CODE_EXECPATH")):
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


# ---------------------------------------------------------------- static checks


def front_matter(text):
    """(fields, problems) of a Markdown file's YAML front matter, read strictly: `key: value` lines only, and a plain
    (unquoted) value may hold nothing a strict YAML parser reads as syntax."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, ["no front matter"]
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return None, ["front matter never closes"]
    fields, problems = {}, []
    for number, line in enumerate(lines[1:end], 2):
        if not line.strip():
            continue
        match = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):(?: (.*))?$", line)
        if not match:
            problems.append(f"line {number}: not a `key: value` line")
            continue
        key, value = match.group(1), (match.group(2) or "").strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            inner = value[1:-1]
            if value[0] == '"' and re.search(r'(?<!\\)"', inner):
                problems.append(f"line {number}: `{key}` holds an unescaped double quote")
            value = inner.replace('\\"', '"') if value[0] == '"' else inner.replace("''", "'")
        else:
            for trap in PLAIN_SCALAR_TRAPS:
                if trap in value:
                    problems.append(f"line {number}: `{key}` is unquoted and holds `{trap}`; quote the value")
            if value.startswith(RESERVED_STARTS) and not re.fullmatch(r"\[[^\[\]{}]*\]", value):
                problems.append(f"line {number}: `{key}` is unquoted and starts with `{value[0]}`; quote the value")
        fields[key] = value
    return fields, problems


def check_front_matter(root):
    problems = []
    for plugin in plugins(root):
        base = os.path.relpath(plugin, root).replace("\\", "/")
        for path in tracked(root, f"{base}/skills/*/SKILL.md", f"{base}/agents/*.md", f"{base}/output-styles/*.md"):
            with open(os.path.join(root, path), encoding="utf-8") as handle:
                fields, found = front_matter(handle.read())
            problems += [f"{path}: {p}" for p in found]
            if fields is None:
                continue
            for key in ("name", "description"):
                if not fields.get(key):
                    problems.append(f"{path}: front matter lacks `{key}`")
            if len(fields.get("description", "")) > 1024:
                problems.append(f"{path}: description is over 1,024 characters")
            if path.endswith("/SKILL.md") and fields.get("name") != path.split("/")[-2]:
                problems.append(f"{path}: name `{fields.get('name')}` is not the folder's name")
            if "/agents/" in path and fields.get("name") != os.path.basename(path)[:-3]:
                problems.append(f"{path}: name `{fields.get('name')}` is not the file's name")
    return problems


def check_modes(root):
    lines = [line for line in git(root, "ls-files", "-s", "--", *EXECUTABLE).splitlines() if line]
    problems = [f"{line.split(chr(9))[-1]}: git mode {line.split()[0]}, must be 100755 (git update-index --chmod=+x)"
                for line in lines if not line.startswith("100755 ")]
    return problems if lines else ["no launcher or bin/ tool is tracked"]


def check_package(root):
    """Size, kind and count of the files each plugin folder ships (the plugin directory holds a version otherwise)."""
    problems = []
    for plugin in plugins(root):
        base = os.path.relpath(plugin, root).replace("\\", "/")
        files = tracked(root, base)
        if len(files) > MAX_FILES:
            problems.append(f"{base}: {len(files)} files, over {MAX_FILES}")
        for path in files:
            full = os.path.join(root, path)
            suffix = os.path.splitext(path)[1].lower()
            size = os.path.getsize(full)
            if suffix in IMAGE_SUFFIXES:
                continue
            if suffix not in TEXT_SUFFIXES:
                problems.append(f"{path}: a {suffix} file is neither text nor an image")
                continue
            if size > MAX_FILE_BYTES:
                problems.append(f"{path}: {size} bytes, over {MAX_FILE_BYTES}")
            with open(full, "rb") as handle:
                if b"\0" in handle.read(8192):
                    problems.append(f"{path}: binary content")
    return problems


def check_readme(root):
    """The plugin README names every userConfig option and every bin/ command."""
    problems = []
    for plugin in plugins(root):
        base = os.path.relpath(plugin, root).replace("\\", "/")
        with open(os.path.join(plugin, "README.md"), encoding="utf-8") as handle:
            readme = handle.read()
        with open(os.path.join(plugin, ".claude-plugin", "plugin.json"), encoding="utf-8") as handle:
            manifest = json.load(handle)
        for key in manifest.get("userConfig", {}):
            if f"`{key}`" not in readme:
                problems.append(f"{base}/README.md: option `{key}` is not named")
        for path in tracked(root, f"{base}/bin/*"):
            if f"`{os.path.basename(path)}" not in readme:
                problems.append(f"{base}/README.md: command `{os.path.basename(path)}` is not named")
    return problems


def check_references(root):
    """Every `<plugin>:<skill>` a skill, agent, README or script names exists, and so does every bin/ command a
    skill names in backticks at the start of a code span."""
    problems = []
    for plugin in plugins(root):
        base = os.path.relpath(plugin, root).replace("\\", "/")
        name = os.path.basename(plugin)
        skills = {p.split("/")[-2] for p in tracked(root, f"{base}/skills/*/SKILL.md")}
        tools = {os.path.basename(p) for p in tracked(root, f"{base}/bin/*")}
        agents = {os.path.basename(p)[:-3] for p in tracked(root, f"{base}/agents/*.md")}
        for path in tracked(root, base):
            if os.path.splitext(path)[1].lower() not in (".md", ".py", ".ts", ".json"):
                continue
            with open(os.path.join(root, path), encoding="utf-8", errors="replace") as handle:
                text = handle.read()
            for cited in sorted(set(re.findall(rf"\b{re.escape(name)}:([a-z][a-z0-9-]+)", text))):
                if cited not in skills and cited not in agents:
                    problems.append(f"{path}: cites `{name}:{cited}`, which is neither a skill nor an agent of the plugin")
            if not path.endswith(".md"):
                continue
            firsts = {span.split()[0] for span in re.findall(r"`([^`\n]+)`", text) if span.split()}
            for word in sorted(w for w in firsts if re.fullmatch(r"[a-z]+(-[a-z]+)+", w)):
                if word not in tools and word not in skills and word not in NOT_COMMANDS:
                    problems.append(f"{path}: names `{word}`, which is neither a bin/ command nor a skill of the plugin")
    return problems


STATIC = [("file modes", check_modes), ("front matter", check_front_matter), ("package files", check_package),
          ("README coverage", check_readme), ("cross-references", check_references)]


# ---------------------------------------------------------------- commands


def commands(root, require_claude):
    """[(label, argv, cwd)] of every command the gate runs, and the notices of what it leaves out."""
    found, notices = [], []
    suites = sorted(tracked(root, "scripts/test_*.py", "tests/test_*.py", "tests/**/test_*.py",
                            "plugins/*/scripts/test_*.py"))
    for path in dict.fromkeys(suites):
        found.append((path, [sys.executable, "-B", os.path.basename(path)], os.path.join(root, os.path.dirname(path))))
    leak = [sys.executable, "-B", os.path.join("scripts", "leak_check.py")]
    if os.path.isfile(os.path.join(root, ".leak-patterns")):
        leak.append("--require-patterns")
    found.append(("leak check", leak, root))
    claude = find_claude()
    if claude:
        for plugin in plugins(root):
            base = os.path.relpath(plugin, root).replace("\\", "/")
            found.append((f"claude plugin test {base}", [claude, "plugin", "test", base], root))
            found.append((f"claude plugin validate {base}",
                          [claude, "plugin", "validate", "--strict", "--profile", "directory", base], root))
        found.append(("claude plugin validate .", [claude, "plugin", "validate", "--strict", "."], root))
    elif require_claude:
        found.append(("claude", [sys.executable, "-c", "import sys; sys.exit('claude not found on PATH')"], root))
    else:
        notices.append("claude not found: `claude plugin test` and `claude plugin validate` were not run")
    return found, notices


def run_one(job):
    label, argv, cwd = job
    started = time.monotonic()
    try:
        out = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             stdin=subprocess.DEVNULL, timeout=1800)
        code, text = out.returncode, (out.stdout or "") + (out.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as error:
        code, text = 1, f"{type(error).__name__}: {error}"
    return label, code, time.monotonic() - started, text


def gate(root, require_claude=False, out=sys.stdout):
    failed = []
    for label, check in STATIC:
        problems = check(root)
        print(f"{'ok  ' if not problems else 'FAIL'}  {label}", file=out, flush=True)
        for problem in problems:
            print(f"        {problem}", file=out, flush=True)
        if problems:
            failed.append(label)
    jobs, notices = commands(root, require_claude)
    with concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        for label, code, seconds, text in pool.map(run_one, jobs):
            print(f"{'ok  ' if code == 0 else 'FAIL'}  {label} ({seconds:.1f} s)", file=out, flush=True)
            if code != 0:
                failed.append(label)
                for line in text.strip().splitlines()[-25:]:
                    print(f"        {line}", file=out, flush=True)
    for notice in notices:
        print(f"note  {notice}", file=out, flush=True)
    print(f"check_all: {'FAILED: ' + ', '.join(failed) if failed else 'all passed'}", file=out, flush=True)
    return 1 if failed else 0


def clean_clone(require_claude):
    """The gate on a fresh clone of HEAD: only what is committed, with the modes the index records."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        target = os.path.join(scratch, "clone")
        subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", REPO, target], check=True)
        print(f"clean clone of {git(REPO, 'rev-parse', '--short', 'HEAD').strip()} in {target}", flush=True)
        argv = [sys.executable, "-B", os.path.join(target, "scripts", "check_all.py")]
        return subprocess.run(argv + (["--require-claude"] if require_claude else []), cwd=target).returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--clean-clone", action="store_true", help="run the gate on a fresh clone of HEAD")
    parser.add_argument("--require-claude", action="store_true", help="fail when the claude command is not found")
    parser.add_argument("--list", action="store_true", help="print what would run and exit")
    args = parser.parse_args(argv)
    if args.list:
        for label, _ in STATIC:
            print(f"static: {label}")
        for label, command, _ in commands(REPO, args.require_claude)[0]:
            print(f"run: {label}")
        return 0
    if args.clean_clone:
        return clean_clone(args.require_claude)
    return gate(REPO, args.require_claude)


if __name__ == "__main__":
    sys.exit(main())
