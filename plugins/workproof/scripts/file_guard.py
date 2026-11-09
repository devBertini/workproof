"""PreToolUse hook for the file tools (Read, Grep, Edit, NotebookEdit): blocks a call that would show a secret file.

The shell guard (secret_guard.py) sees commands only; this one covers the tools that open a file without a shell:
  - Read, Edit and NotebookEdit of a secret file (an edit shows the text around the change);
  - Grep whose `path` is a secret file, or whose `glob` picks secret files (`.env*`, `**/*.pem`).
Creating a file with Write stays allowed, as `> .env` does in the shell guard, and so do the placeholder files
(`.env.example`). A Grep over a folder is not blocked, though it may match lines of a secret file in that folder:
blocking every folder search would stop all work. That is a limit of this guard.

The names are those of secret_patterns.SECRET_NAMES plus the user's `extra_secret_names`. Same switch as the shell
guard (the `secret_guard` option). Input: the hook JSON on stdin. Exit 0 lets the call run; exit 2 blocks it, with the
reason on stderr for Claude. Python 3.10+, standard library only.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from modules import module_enabled  # noqa: E402
from secret_patterns import compile_names, extra_names_from_env, name_kind  # noqa: E402

# The tool input field that names the file each tool opens.
PATH_FIELDS = {"Read": "file_path", "Edit": "file_path", "NotebookEdit": "notebook_path", "Grep": "path"}
GLOB_CHARS = re.compile(r"[*?\[]")
BRACES = re.compile(r"\{([^{}]*)\}")


def expand_braces(glob):
    """`*.{pem,key}` -> [`*.pem`, `*.key`], one level at a time; a glob without braces comes back alone."""
    found = BRACES.search(glob)
    if not found:
        return [glob]
    out = []
    for part in found.group(1).split(","):
        out += expand_braces(glob[:found.start()] + part + glob[found.end():])
    return out


def glob_kind(glob, names):
    """What secret files the glob picks, or None: the glob read as a name (`*.pem`, `**/.env`), or the part before
    its first wildcard (`.env*`, `config/.env.*`)."""
    for one in expand_braces(glob.strip().replace("\\", "/")):
        kind = name_kind(one, names)
        wildcard = GLOB_CHARS.search(one)
        if not kind and wildcard and wildcard.start() > 0:
            kind = name_kind(one[:wildcard.start()].rstrip("."), names) or name_kind(one[:wildcard.start()], names)
        if kind:
            return kind
    return None


def verdict(tool, tool_input, names=None):
    """None when the call may run, or the reason it is blocked. `names` comes from compile_names."""
    if tool not in PATH_FIELDS or not isinstance(tool_input, dict):
        return None
    if names is None:
        names = compile_names()
    path = tool_input.get(PATH_FIELDS[tool])
    if isinstance(path, str) and path.strip():
        kind = name_kind(path.strip(), names)
        if kind:
            return f"the {tool} tool is given the secret file `{path.strip()}` ({kind})"
    glob = tool_input.get("glob") if tool == "Grep" else None
    if isinstance(glob, str) and glob.strip():
        kind = glob_kind(glob, names)
        if kind:
            return f"the Grep tool searches inside the files `{glob.strip()}` picks ({kind})"
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
    reason = verdict(data.get("tool_name"), data.get("tool_input"), compile_names(extra_names_from_env()))
    if reason is None:
        return 0
    print(f"Blocked by workproof (secret-guard module): {reason}. Secrets stay out of the conversation: refer to "
          f"them by path and let the tool that needs them read them. To add a line to such a file without reading "
          f"it, append with the shell (`>>`); creating it with Write is allowed. If this is a false alarm, ask the "
          f"user instead of working around the guard (skill workproof:guarding-secrets).", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
