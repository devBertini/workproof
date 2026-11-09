#!/usr/bin/env python
"""Secret formats in the staged content of a git repository.

    staged_secrets.py <repo>

Reads every staged file as the index holds it (the blob a commit would take, never the working copy) and applies
SECRET_CONTENT from secret_patterns.py. stdout: one `file:line: kind` line per FAIL finding; the matched value is
never printed. stderr: notes (files skipped as binary or over the size limit) and WARN findings.
Exit 0 clean, 1 a FAIL finding, 2 the check itself could not run (a refusal, never a pass).
Python 3.10+, standard library only.
"""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import secret_patterns  # noqa: E402

GITLINK = "160000"


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, check=True).stdout


def staged_blobs(repo):
    """(path, blob sha, mode) of every added, copied, modified or renamed staged path; deletions hold no content."""
    raw = git(repo, "diff", "--cached", "--raw", "-z", "--no-renames", "--diff-filter=ACMR").split(b"\0")
    entries = []
    for i in range(0, len(raw) - 1, 2):
        meta = raw[i].decode().split()  # :oldmode newmode oldsha newsha status
        entries.append((raw[i + 1].decode("utf-8", "surrogateescape"), meta[3], meta[1]))
    return entries


def scan(repo):
    """(failures, warnings, notes): lists of strings. Raises when the check cannot run."""
    patterns = [(level, kind, re.compile(pattern)) for level, kind, pattern in secret_patterns.SECRET_CONTENT]
    limit = secret_patterns.SCAN_MAX_BYTES
    failures, warnings, notes = [], [], []
    for path, sha, mode in staged_blobs(repo):
        if mode == GITLINK:
            continue
        size = int(git(repo, "cat-file", "-s", sha))
        if size > limit:
            notes.append(f"{path} is {size} bytes, over {limit}; content not scanned")
            continue
        data = git(repo, "cat-file", "blob", sha)
        if b"\0" in data[:8192]:
            notes.append(f"{path} is binary; content not scanned")
            continue
        text = data.decode("utf-8", "replace")
        seen = set()
        for level, kind, pattern in patterns:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                if (kind, line) in seen:
                    continue
                seen.add((kind, line))
                (failures if level == "FAIL" else warnings).append(f"{path}:{line}: {kind}")
    return failures, warnings, notes


def main(argv):
    if len(argv) != 2:
        print("usage: staged_secrets.py <repo>", file=sys.stderr)
        return 2
    try:
        failures, warnings, notes = scan(argv[1])
    except Exception as error:  # fail closed: a check that cannot run never counts as a pass
        print(f"staged_secrets: cannot check staged content: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    for note in notes:
        print(f"staged_secrets: note: {note}", file=sys.stderr)
    for warning in warnings:
        print(f"staged_secrets: warning: {warning}", file=sys.stderr)
    for failure in failures:
        print(failure)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
