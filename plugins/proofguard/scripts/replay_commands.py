#!/usr/bin/env python
"""Replays the shell commands found in Claude Code transcripts through the guard and reports counts.

    replay_commands.py <transcript.jsonl | folder>... [--skip <text in path>]... [--show-blocked]

Every `tool_use` block of the Bash and PowerShell tools in each transcript (folders are searched for *.jsonl) is
run through secret_guard.verdict. The report holds counts only: commands replayed, unique commands, blocked ones,
and the blocks grouped by the kind of rule that fired. Commands are never written anywhere. With --show-blocked
each blocked command is also printed to the terminal, cut to 160 characters, so a person can review it.
--skip leaves out transcript files whose path contains the text (for example the session doing the replay).
Python 3.10+, standard library only.
"""
import collections
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import secret_guard  # noqa: E402
import secret_patterns  # noqa: E402

TOOLS = ("Bash", "PowerShell")


def transcript_files(paths):
    for path in paths:
        if os.path.isdir(path):
            for folder, _, files in os.walk(path):
                for name in files:
                    if name.endswith(".jsonl"):
                        yield os.path.join(folder, name)
        elif os.path.isfile(path):
            yield path


def commands_in(path):
    """(tool, command) for each shell tool call in one transcript file; unreadable lines are skipped."""
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if '"tool_use"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            content = (entry.get("message") or {}).get("content") if isinstance(entry, dict) else None
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") in TOOLS:
                    command = (block.get("input") or {}).get("command")
                    if isinstance(command, str):
                        yield block["name"], command


def category(reason):
    """The rule behind a reason, without the file name: the kind in parentheses, or the Docker rule."""
    if "docker" in reason.lower() or "container" in reason.lower():
        return "docker: " + re.sub(r"`[^`]*`", "`..`", reason)[:60]
    kinds = re.findall(r"\(([^()]*(?:\([^()]*\))?[^()]*)\)\s*$", reason)
    return "secret file: " + (kinds[-1] if kinds else "unknown")


def replay(paths, show=False, skip=(), out=sys.stdout):
    names = secret_patterns.compile_names(secret_patterns.extra_names_from_env())
    total, seen, blocked_unique = 0, set(), set()
    blocked = 0
    kinds = collections.Counter()
    for path in transcript_files(paths):
        if any(text in path for text in skip):
            continue
        for tool, command in commands_in(path):
            total += 1
            key = hashlib.sha256((tool + "\0" + command).encode("utf-8", "replace")).hexdigest()
            text = command.replace("\\", "/") if tool == "PowerShell" else command
            reason = secret_guard.verdict(text, names)
            seen.add(key)
            if reason is None:
                continue
            blocked += 1
            blocked_unique.add(key)
            kinds[category(reason)] += 1
            if show:
                print(f"[{tool}] {command[:160]!r} -> {reason[:100]}", file=out)
    print(f"commands replayed: {total}", file=out)
    print(f"unique commands: {len(seen)}", file=out)
    print(f"blocked: {blocked} ({len(blocked_unique)} unique)", file=out)
    for kind, count in kinds.most_common():
        print(f"  {count:4d}  {kind}", file=out)
    return 0


def main(argv):
    args = argv[1:]
    show = "--show-blocked" in args
    skip = [args[i + 1] for i in range(len(args) - 1) if args[i] == "--skip"]
    paths = [a for i, a in enumerate(args) if a != "--show-blocked" and a != "--skip"
             and not (i > 0 and args[i - 1] == "--skip")]
    if not paths:
        print(__doc__, file=sys.stderr)
        return 2
    return replay(paths, show, skip)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
