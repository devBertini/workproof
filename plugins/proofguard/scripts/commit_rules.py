"""Optional commit rules that guarded-commit applies before it stages and scans (commit rules module).

Each rule is off until its environment variable turns it on (the `env` block of a Claude Code settings file, since
command-line tools of a plugin do not receive its options):
- PROOFGUARD_COMMIT_CONVENTIONAL=on, or PROOFGUARD_COMMIT_TYPES set: the message must be one Conventional Commit line
  (`type(scope)!: text`) with a type from PROOFGUARD_COMMIT_TYPES (comma separated; default feat, fix, docs, style,
  refactor, perf, test, build, ci, chore, revert).
- PROOFGUARD_COMMIT_MAX_MB=<n>: no staged file larger than n megabytes.
- PROOFGUARD_COMMIT_BLOCK_NEW=<globs>: no newly added path matching a glob (comma or newline separated; `*` stays in
  one folder, `**` crosses folders, a glob matches a path suffix; a glob starting with `!` is an exception), for
  example generated captures: `docs/**/*.png, !docs/reference/**`.
A broken rule refuses the commit. Standard library.
"""
import os
import re

import secret_patterns

DEFAULT_TYPES = "feat,fix,docs,style,refactor,perf,test,build,ci,chore,revert"
ON = {"true", "1", "yes", "on"}


class RuleBroken(Exception):
    pass


def conventional_types(environ):
    raw = environ.get("PROOFGUARD_COMMIT_TYPES", "").strip()
    if not raw and environ.get("PROOFGUARD_COMMIT_CONVENTIONAL", "").strip().lower() not in ON:
        return None
    return [t.strip() for t in (raw or DEFAULT_TYPES).split(",") if t.strip()]


def check_message(message, environ):
    types = conventional_types(environ)
    if types is None:
        return
    text = message.strip()
    if "\n" in text or "\r" in text:
        raise RuleBroken("the message must be one line")
    pattern = r"^(" + "|".join(map(re.escape, types)) + r")(\([A-Za-z0-9._/-]+\))?!?: \S.*$"
    if not re.match(pattern, text):
        raise RuleBroken("the message is not a Conventional Commit line (`type(scope): text`, type one of "
                         + ", ".join(types) + "): " + text)


def block_rules(environ):
    blocked, allowed = [], []
    for glob in secret_patterns.split_globs(environ.get("PROOFGUARD_COMMIT_BLOCK_NEW", "")):
        target = allowed if glob.startswith("!") else blocked
        target.append(re.compile(secret_patterns.glob_to_regex(glob.lstrip("!")), re.IGNORECASE))
    return blocked, allowed


def max_bytes(environ):
    raw = environ.get("PROOFGUARD_COMMIT_MAX_MB", "").strip()
    if not raw:
        return 0
    try:
        value = float(raw)
    except ValueError:
        raise RuleBroken(f"PROOFGUARD_COMMIT_MAX_MB is not a number: {raw}") from None
    return int(value * 1_000_000) if value > 0 else 0


def path_rules_on(environ):
    return bool(max_bytes(environ) or block_rules(environ)[0])


def check_staged(repo, added, present, environ):
    """`added`: newly added paths; `present`: paths that will exist after the commit (both relative to repo)."""
    blocked, allowed = block_rules(environ)
    new = [name for name in added
           if any(p.search(name) for p in blocked) and not any(p.search(name) for p in allowed)]
    if new:
        raise RuleBroken("new files match PROOFGUARD_COMMIT_BLOCK_NEW: " + ", ".join(new))
    limit = max_bytes(environ)
    if limit:
        big = [name for name in present
               if os.path.isfile(os.path.join(repo, name)) and os.path.getsize(os.path.join(repo, name)) > limit]
        if big:
            raise RuleBroken(f"files over {limit / 1_000_000:g} MB: " + ", ".join(big))
