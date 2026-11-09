#!/usr/bin/env python
"""A local commit that refuses secrets.

    guarded_commit.py <repo> "<message>" <path>...

Stages exactly the given paths (additions, changes and deletions; every path is taken literally, so `file[1].txt`,
`*` or a leading `:` never match anything but that exact name), then refuses, committing nothing and leaving
nothing staged, when a staged file name matches a secret file name (secret_patterns.SECRET_NAMES plus the user's
extra_secret_names, when the environment carries them) or the staged content matches a secret format
(secret_patterns.SECRET_CONTENT; every text blob is scanned whatever its size, UTF-16 and UTF-32 text included, and
only real binary content is skipped, with a note). Otherwise it commits with the message as given. It never pushes.
Optional commit rules (Conventional Commit line, size cap, blocked new files) apply first when the environment turns
them on: see commit_rules.py. Exit 0 committed, 1 refused or failed. Python 3.10+, standard library only.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import commit_rules  # noqa: E402
import secret_patterns  # noqa: E402
import staged_secrets  # noqa: E402


class Refused(Exception):
    pass


def git(repo, *args, check=True):
    # --literal-pathspecs: a path argument is a file name, never a glob or pathspec magic (`*`, `[1]`, `:/`, `:(glob)`).
    result = subprocess.run(["git", "--literal-pathspecs", "-C", repo, *args], capture_output=True,
                            env=staged_secrets.git_env())
    if check and result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise Refused(f"git {args[0]} failed" + (f": {detail}" if detail else ""))
    return result


def staged_names(repo):
    raw = git(repo, "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR").stdout
    return [name.decode("utf-8", "surrogateescape") for name in raw.split(b"\0") if name]


def unstage(repo):
    git(repo, "reset", "-q", check=False)


def commit(repo, message, paths):
    if git(repo, "rev-parse", "--git-dir", check=False).returncode != 0:
        raise Refused(f"{repo} is not a git repository")
    if not message.strip():
        raise Refused("the commit message is empty")
    try:
        commit_rules.check_message(message, os.environ)
    except commit_rules.RuleBroken as error:
        raise Refused(f"{error}, nothing committed") from None
    if git(repo, "diff", "--cached", "--quiet", check=False).returncode != 0:
        raise Refused("something is already staged; unstage it first (the commit carries only the given paths)")
    try:
        git(repo, "add", "-A", "--", *paths)
        names = staged_names(repo)
        try:
            added = [n.decode("utf-8", "surrogateescape") for n in
                     git(repo, "diff", "--cached", "--name-only", "-z", "--diff-filter=A").stdout.split(b"\0") if n]
            commit_rules.check_staged(repo, added, names, os.environ)
        except commit_rules.RuleBroken as error:
            raise Refused(f"{error}, nothing committed") from None
        if not git(repo, "diff", "--cached", "--name-only", "-z").stdout:
            raise Refused("nothing to commit in those paths")
        compiled = secret_patterns.compile_names(secret_patterns.extra_names_from_env())
        bad = [f"{name} ({secret_patterns.name_kind(name, compiled)})" for name in names
               if secret_patterns.name_kind(name, compiled)]
        if bad:
            raise Refused("secret file names are staged, nothing committed: " + "; ".join(bad))
        try:
            failures, warnings, notes = staged_secrets.scan(repo)
        except Exception as error:  # fail closed
            raise Refused(f"staged content could not be checked for secrets ({type(error).__name__}), "
                          "nothing committed") from None
        for text in notes:
            print(f"guarded-commit: note: {text}", file=sys.stderr)
        for text in warnings:
            print(f"guarded-commit: warning: {text}", file=sys.stderr)
        if failures:
            raise Refused("secret formats found in staged content, nothing committed (file:line: kind): "
                          + "; ".join(failures))
        stat = git(repo, "diff", "--cached", "--stat").stdout.decode("utf-8", "replace").strip().splitlines()
        git(repo, "commit", "-q", "-m", message)
    except Refused:
        unstage(repo)
        raise
    if stat:
        print(stat[-1])
    print(git(repo, "log", "--oneline", "-1").stdout.decode("utf-8", "replace").strip())


def main(argv):
    if len(argv) < 4:
        print('usage: guarded-commit <repo> "<message>" <path>...', file=sys.stderr)
        return 1
    try:
        commit(argv[1], argv[2], argv[3:])
    except Refused as error:
        print(f"guarded-commit: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
