"""Stops personal data from reaching a public repository.

Scans every file git would publish (tracked, plus untracked files that are not ignored),
each commit's author, committer and message, and reports:
  - a hit of any regex listed in a private patterns file (default: .leak-patterns at the
    repository root, kept out of git), matched case-insensitively;
  - an absolute path inside a user's home folder (Windows, macOS, Linux, Git Bash, WSL),
    except for placeholder user names;
  - an email address outside the placeholder domains and GitHub's noreply domain.

A finding names the file and line and which rule matched, never the matched text.
Exit 0 when clean, 1 on any finding, 2 on a usage error. Python 3 standard library.
"""
import argparse
import os
import re
import subprocess
import sys

PLACEHOLDER_USERS = r"(?:user|username|runner|me|you|alice|bob|example|name|<[^>]+>|\$\w+|%\w+%)"
HOME_PATHS = re.compile(
    r"(?i)(?:\b[a-z]:[\\/]+users[\\/]+|(?<![\w.])/(?:mnt/[a-z]/|[a-z]/)?users/|(?<![\w.])/home/)"
    r"(?!" + PLACEHOLDER_USERS + r"(?:[\\/\s\"'`)]|$))[\w.-]+"
)
EMAIL = re.compile(r"\b[\w.+-]+@([\w-]+(?:\.[\w-]+)+)\b")
ALLOWED_EMAIL_DOMAINS = re.compile(
    r"(?i)^(?:users\.noreply\.github\.com|example\.(?:com|org|net)|[\w.-]*\.example|[\w.-]*\.test|[\w.-]*\.invalid|localhost)$"
)
SKIP_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".gz", ".zip", ".pdf")


def git(root, *args):
    out = subprocess.run(["git", "-C", root, *args], capture_output=True)
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {out.stderr.decode(errors='replace').strip()}")
    return out.stdout.decode("utf-8", errors="replace")


def load_patterns(path):
    patterns = []
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            line = line.rstrip("\r\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            try:
                patterns.append((number, re.compile(line, re.IGNORECASE)))
            except re.error as error:
                raise ValueError(f"{path}:{number}: bad regex ({error})") from None
    return patterns


def scan_text(text, patterns):
    """Yields (line number, rule) for each finding in text."""
    for number, line in enumerate(text.splitlines(), 1):
        for pattern_line, pattern in patterns:
            if pattern.search(line):
                yield number, f"private pattern on line {pattern_line} of the patterns file"
        if HOME_PATHS.search(line):
            yield number, "absolute path in a user's home folder"
        for match in EMAIL.finditer(line):
            if not ALLOWED_EMAIL_DOMAINS.match(match.group(1)):
                yield number, "email address"


def published_files(root):
    listing = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return sorted({name for name in listing.split("\0") if name})


def scan_repository(root, patterns):
    findings = []
    for name in published_files(root):
        path = os.path.join(root, name)
        if name.lower().endswith(SKIP_SUFFIXES) or not os.path.isfile(path):
            continue
        for number, rule in scan_text(name, patterns):
            findings.append(f"{name}: file name: {rule}")
        with open(path, "rb") as handle:
            data = handle.read()
        if b"\0" in data[:8192]:
            continue
        for number, rule in scan_text(data.decode("utf-8", errors="replace"), patterns):
            findings.append(f"{name}:{number}: {rule}")
    if _has_head(root):
        log = git(root, "log", "-z", "--format=%h%n%an <%ae>%n%cn <%ce>%n%B")
        for entry in filter(None, log.split("\0")):
            commit, _, body = entry.strip("\n").partition("\n")
            for number, rule in scan_text(body, patterns):
                findings.append(f"commit {commit}: line {number} of its author, committer and message: {rule}")
    return findings


def _has_head(root):
    return subprocess.run(["git", "-C", root, "rev-parse", "--verify", "--quiet", "HEAD"],
                          capture_output=True).returncode == 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="repository to scan (default: current folder)")
    parser.add_argument("--patterns", help="private patterns file (default: <root>/.leak-patterns)")
    parser.add_argument("--require-patterns", action="store_true",
                        help="fail when the patterns file is missing (use on the maintainer's machine)")
    args = parser.parse_args(argv)
    try:
        root = git(args.root, "rev-parse", "--show-toplevel").strip()
    except RuntimeError as error:
        print(f"leak-check: {error}", file=sys.stderr)
        return 2
    patterns_path = args.patterns or os.path.join(root, ".leak-patterns")
    patterns = []
    if os.path.isfile(patterns_path):
        try:
            patterns = load_patterns(patterns_path)
        except ValueError as error:
            print(f"leak-check: {error}", file=sys.stderr)
            return 2
    elif args.require_patterns:
        print(f"leak-check: patterns file not found: {patterns_path}", file=sys.stderr)
        return 2
    else:
        print("leak-check: no patterns file; running the generic checks only", file=sys.stderr)
    findings = scan_repository(root, patterns)
    for finding in findings:
        print(finding)
    if findings:
        print(f"leak-check: {len(findings)} finding(s)", file=sys.stderr)
        return 1
    print(f"leak-check: clean ({len(published_files(root))} files, {len(patterns)} private patterns)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
