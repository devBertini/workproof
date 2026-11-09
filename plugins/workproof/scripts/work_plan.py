#!/usr/bin/env python
"""The active plan of a piece of work done in stages (command `work-plan` of the workproof plugin, module proof_plans).

One plan per project, `<project>/docs/ACTIVE-PLAN.json`. The project is the git top-level of the current folder (or of
`--where <folder>`); outside a git repository it is the folder itself. The plan is a stage list in JSON that is only
ever marked, never edited away, with notes written while a stage runs and a short state a fresh context reads first.

  work-plan new --goal "<the order, quoted>" [--where <project folder>]
  work-plan add --title "<stage>" --done-when "<acceptance and its proof>" [--where ...]
  work-plan start N | note N "<text>" | done N --result "<...>" --proof "<command or file>"
  work-plan done N --result "<...>" [--timeout S] --bundle -- <command...>
  work-plan drop N --reason "<why it is dropped>"
  work-plan pause N --reason "<what it waits on>"  (back to pending, so another stage can start)
  work-plan show | finish        (finish deletes the plan once every stage is closed)
  work-plan hook                 (SessionStart: reads the hook JSON on stdin; prints the current project's plan)

`done --bundle` is the verifiable proof: the command after `--` runs through verify_run.py from the plan's project
folder, bounded by --timeout (default 600 s). Exit 0 closes the stage and stores the evidence under it as `verified`
(command, exit, duration, sha256 of the whole output, the last lines trimmed to 2,000 characters, time); the proof is
written as `verified: <command> exit 0 in <s> s`. Any other outcome (non-zero exit, timeout, a refused outward
command) leaves the stage open and prints why. `show` and `hook` mark such a stage `[verified]`. `--proof` stays for a
proof that is not a command (nothing runs): such a stage is marked `[unverified]`; the two are never given together.
The command runs with no lock held; the plan is reloaded afterwards, and a stage no longer in progress is not closed.

Every command that changes a plan holds `<plan>.lock` for its load-modify-save, and the file is written through a
temp file of its own, so parallel commands never crash or lose each other's changes.

Plans root (optional). A folder that holds many projects, named by the environment variable WORKPROOF_PLANS_ROOT or, in a
hook, by the plugin option `plans_root` (exported by Claude Code as CLAUDE_PLUGIN_OPTION_PLANS_ROOT). When set:
the SessionStart hook also prints one index line for every other active plan under it (up to three folders deep);
a plan under it is labelled by its path from it; the plan of a session started in the root folder itself lives at
`<root>/.claude/ACTIVE-PLAN.json`; and each command that changes a plan writes `<root>/.claude/plan-sessions/<session
id>`, the pointer the hook follows. When unset, none of that exists: the hook prints the current project's plan alone.

The hook output is capped at 9,500 characters (Claude Code keeps only 10,000 characters of a hook's output, then a
2,000-character preview). Over that, every closed stage but the newest five folds into one `[closed]` line of ids
before notes or index lines go. A command that changes a plan prints only the stage it touched, the open stages and
the next one; `show` prints all. The hook prints nothing when WORKPROOF_PLANS_SILENT is set (a headless session that must
not take steps of a plan it does not own).
"""
import argparse
import contextlib
import datetime
import glob
import json
import os
import random
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from modules import module_enabled  # noqa: E402

NAME = "ACTIVE-PLAN.json"
OPEN = ("pending", "in_progress")
CAP = 9500  # Claude Code keeps 10,000 characters of a hook's output; the rest becomes a 2,000-character preview
POINTER_DIR = os.path.join(".claude", "plan-sessions")
POINTER_DAYS = 14
ROOT_ENV = "WORKPROOF_PLANS_ROOT"
ROOT_OPTION_ENV = "CLAUDE_PLUGIN_OPTION_PLANS_ROOT"
SILENT_ENV = "WORKPROOF_PLANS_SILENT"
# The names both variables had before the plugin was renamed: still read, no longer documented.
OLD_ROOT_ENV = "PROOF_PLANS_ROOT"
OLD_SILENT_ENV = "PROOF_PLANS_SILENT"
BUNDLE_TIMEOUT = 600
WINDOWS = os.name == "nt"
LOCK_WAIT = 30.0    # seconds a command waits for another one's lock before it gives up
LOCK_STALE = 120.0  # a lock older than this belongs to a process that died: it is broken (a held lock lasts milliseconds)
REPLACE_WAIT = 5.0  # seconds os.replace is retried on Windows, where a reader or a scanner can hold the target
TAIL_CHARS = 2000
SKILL = "workproof:working-in-stages"
HEADER_INDEX = (f"Active plans under the plans root (skill {SKILL}). Continue the plan of the project you work on"
                " from its now or next stage; read the stage's notes before acting.")
HEADER_PROJECT = (f"Active plan of this project (skill {SKILL}): continue from its now or next stage; read the"
                  " stage's notes before acting.")
HEADER_COMPACT = ("Context was compacted. Invoked skills came back only in part (at most 5,000 tokens each, 25,000 in all):"
                  " invoke again those the open stage needs, then continue this plan from its now stage and its notes.")
AUTO = object()  # main(root=AUTO): read the plans root from the environment


def plans_root():
    """The configured plans root, or None: WORKPROOF_PLANS_ROOT, else the plugin option Claude Code exports to hooks.
    A value that is not an existing folder counts as unset."""
    value = (os.environ.get(ROOT_ENV) or os.environ.get(OLD_ROOT_ENV) or os.environ.get(ROOT_OPTION_ENV) or "").strip()
    if not value:
        return None
    value = os.path.normpath(os.path.expanduser(value))
    return value if os.path.isdir(value) else None


def same_path(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def project_top(folder):
    """The git top-level that holds `folder` (the nearest parent with a .git folder or file), or None."""
    probe = os.path.abspath(folder)
    while True:
        if os.path.exists(os.path.join(probe, ".git")):
            return probe
        parent = os.path.dirname(probe)
        if parent == probe:
            return None
        probe = parent


def project_folder(where=None):
    """The project a command works on: the git top-level of `where` (or of the current folder), else that folder."""
    folder = os.path.abspath(where or os.getcwd())
    if where and not os.path.isdir(folder):
        sys.exit(f"work_plan: {where} is not a folder")
    return project_top(folder) or folder


def root_plan(root):
    return os.path.join(root, ".claude", NAME)


def plan_path(where=None, root=None):
    """The plan file of the project that holds `where` or the current folder; the root's own plan when that project is
    the plans root."""
    project = project_folder(where)
    if root and same_path(project, root):
        return root_plan(root)
    return os.path.join(project, "docs", NAME)


def load(path):
    if not os.path.exists(path):
        sys.exit(f"work_plan: no active plan at {path}; start one with `new`")
    deadline = time.monotonic() + REPLACE_WAIT
    while True:
        try:
            with open(path, encoding="utf-8") as f:
                plan = json.load(f)
            break
        except ValueError as error:
            sys.exit(f"work_plan: {path} is not a readable plan ({error}); fix or remove the file")
        except PermissionError:  # Windows: the file is being replaced at this very moment
            if not WINDOWS or time.monotonic() >= deadline:
                raise
            time.sleep(0.01)
    if not isinstance(plan, dict) or not isinstance(plan.get("stages"), list) or "goal" not in plan:
        sys.exit(f"work_plan: {path} is not a readable plan (no goal or stage list); fix or remove the file")
    return plan


def replace_retrying(src, dst):
    """os.replace, retried for a short bounded time on Windows, where it raises PermissionError while another process
    has the target open (a concurrent reader, an indexer, a virus scanner)."""
    deadline = time.monotonic() + REPLACE_WAIT
    while True:
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if not WINDOWS or time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


def write_atomic(path, text):
    """Write `text` to `path` so a reader sees the old file or the new one, never half: a temp file of its own name (so
    two writers never share one) in the same folder, flushed to disk, then renamed over the target. LF on every
    platform: Python's text mode writes CRLF on Windows, and the plan is usually a tracked file."""
    folder = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=f".{os.path.basename(path)}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)  # mkstemp makes the file private; a plan is an ordinary project file
        replace_retrying(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def save(path, plan):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_atomic(path, json.dumps(plan, indent=1, ensure_ascii=False) + "\n")


@contextlib.contextmanager
def plan_lock(path, create=False):
    """Hold the plan's lock for one load-modify-save, so parallel commands never lose each other's changes.

    The lock is a file `<plan>.lock` made with O_CREAT|O_EXCL, which is atomic on every platform and needs neither
    msvcrt nor fcntl; it is removed on release, so no stray file stays beside the plan. Whoever finds it taken retries
    with short random sleeps up to LOCK_WAIT seconds, then exits with a message. A holder keeps it for milliseconds (no
    command runs under it), so a lock older than LOCK_STALE is a crashed process's and is broken. Two commands breaking
    the same stale lock at the same instant could both pass; that needs a crash and a tie and is accepted.
    When the plan's folder does not exist and `create` is not set there is nothing to lock (the command then fails on
    the missing plan without creating folders)."""
    folder = os.path.dirname(path)
    if create:
        os.makedirs(folder, exist_ok=True)
    elif not os.path.isdir(folder):
        yield
        return
    lock = path + ".lock"
    deadline = time.monotonic() + LOCK_WAIT
    delay = 0.005
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            pass
        except PermissionError:  # Windows reports a lock that is being deleted this way
            if not WINDOWS:
                raise
        try:
            if time.time() - os.path.getmtime(lock) > LOCK_STALE:
                os.remove(lock)
                continue
        except OSError:
            pass
        if time.monotonic() >= deadline:
            sys.exit(f"work_plan: {lock} stayed locked for {LOCK_WAIT:g} s; if no work-plan command is running, "
                     "delete that file")
        time.sleep(delay * (1 + random.random()))
        delay = min(delay * 2, 0.1)
    try:
        os.write(fd, str(os.getpid()).encode())
    finally:
        os.close(fd)
    try:
        yield
    finally:
        deadline = time.monotonic() + REPLACE_WAIT
        while True:
            try:
                os.remove(lock)
                break
            except FileNotFoundError:
                break
            except PermissionError:
                if not WINDOWS or time.monotonic() >= deadline:
                    break
                time.sleep(0.01)


def stage(plan, n):
    for s in plan["stages"]:
        if s["id"] == n:
            return s
    sys.exit(f"work_plan: no stage {n}")


def clip(text, n):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:max(n - 3, 0)] + "..."


KEEP_CLOSED = 5  # closed stages a folded hook text still lists by title, the newest by id


def id_ranges(ids):
    """[1, 2, 3, 5, 7, 8] -> "1-3, 5, 7-8"."""
    spans = []
    for i in sorted(ids):
        if spans and i == spans[-1][1] + 1:
            spans[-1][1] = i
        else:
            spans.append([i, i])
    return ", ".join(f"{a}-{b}" if b > a else f"{a}" for a, b in spans)


def stage_lines(s, brief=False, drop_notes=0, short=False, notes=True):
    """One stage as text: a closed stage is one line (its number and title only when `brief`); an open stage is its
    line and its notes, the oldest `drop_notes` of the stage in progress left out."""
    if s["status"] == "done":
        seal = " [verified]" if s.get("verified") else " [unverified]"
        return [f"  [done] {s['id']}. {s['title']}{seal}" if brief else
                f"  [done] {s['id']}. {s['title']}{seal}: {s.get('result', '')} (proof: {s.get('proof', '')})"]
    if s["status"] == "dropped":
        return [f"  [dropped] {s['id']}. {s['title']}" if brief else
                f"  [dropped] {s['id']}. {s['title']}: {s.get('result', '')}"]
    mark = "now" if s["status"] == "in_progress" else "todo"
    done_when = clip(s["done_when"], 120) if short else s["done_when"]
    lines = [f"  [{mark}] {s['id']}. {s['title']} | done when: {done_when}"]
    if not notes:
        return lines
    stage_notes = s.get("notes", [])
    if mark == "now" and drop_notes:
        lines.append(f"        - ({min(drop_notes, len(stage_notes))} older notes left out; the plan file has them)")
        stage_notes = stage_notes[drop_notes:]
    lines.extend(f"        - {note}" for note in stage_notes)
    return lines


def next_line(plan):
    nxt = next((s for s in plan["stages"] if s["status"] == "in_progress"), None) or \
        next((s for s in plan["stages"] if s["status"] == "pending"), None)
    return (f"  next: stage {nxt['id']}, {nxt['title']}" if nxt else
            "  every stage is closed: move what lasts into the project's documents, then `work-plan finish`")


def render(plan, label, brief=False, drop_notes=0, short=False, fold=False):
    """The plan as text. `brief` (the hook): a closed stage is its number and title only, and `drop_notes` leaves out
    the oldest notes of the stage in progress; `short` also clips the goal and every done-when; `fold` lists only the
    newest KEEP_CLOSED closed stages and the others as one line of their ids, so the open stages always fit."""
    goal = clip(plan["goal"], 300) if short else plan["goal"]
    lines = [f"ACTIVE PLAN {label}", f"  goal: {goal}"]
    closed = [s["id"] for s in plan["stages"] if s["status"] not in OPEN]
    folded = set(sorted(closed)[:-KEEP_CLOSED]) if fold and len(closed) > KEEP_CLOSED else set()
    for s in plan["stages"]:
        if s["id"] in folded:
            if s["id"] == min(folded):
                lines.append(f"  [closed] {id_ranges(folded)} ({len(folded)} stages; `work-plan show` lists them)")
            continue
        lines.extend(stage_lines(s, brief, drop_notes, short))
    lines.append(next_line(plan))
    return "\n".join(lines)


def render_change(plan, label, touched):
    """What a command that changed the plan prints: the stage it touched in full, the other open stages, the next
    one. `show` prints the whole plan; this keeps a long plan from filling the session's context at every command."""
    count = {k: sum(1 for s in plan["stages"] if s["status"] == k) for k in ("done", "dropped", "pending", "in_progress")}
    lines = [f"ACTIVE PLAN {label}: {count['done']} done, {count['dropped']} dropped, "
             f"{count['pending'] + count['in_progress']} open (`work-plan show` lists every stage)"]
    for s in plan["stages"]:
        if s["id"] == touched:
            lines.extend(stage_lines(s))
        elif s["status"] in OPEN:
            lines.extend(stage_lines(s, notes=False))
    lines.append(next_line(plan))
    return "\n".join(lines)


def active_plans(root):
    """Every plan file under the plans root, up to three folders deep, and the root's own plan."""
    found = set()
    for depth in (1, 2, 3):
        pattern = os.path.join(root, *(["*"] * depth), "docs", NAME)
        found.update(glob.glob(pattern))
    paths = sorted(found)
    if os.path.exists(root_plan(root)):
        paths.append(root_plan(root))
    return paths


def session_id_from_env():
    """The session id a Bash command sees: CLAUDE_CODE_SESSION_ID (documented: it matches the hook's session_id)."""
    return os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID") or ""


def safe_session_id(value):
    value = value if isinstance(value, str) else ""
    return value if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value) else ""


def write_pointer(root, plan_file, session_id=None):
    """Record which plan this session works on (only with a plans root). Never fails the command it rides on."""
    sid = safe_session_id(session_id if session_id is not None else session_id_from_env())
    if not root or not sid:
        return
    folder = os.path.join(root, POINTER_DIR)
    try:
        os.makedirs(folder, exist_ok=True)
        write_atomic(os.path.join(folder, sid), os.path.abspath(plan_file) + "\n")
    except OSError:
        pass


def read_pointer(root, sid):
    """The plan file the session's pointer names, when it still exists."""
    sid = safe_session_id(sid)
    if not root or not sid:
        return None
    pointer = os.path.join(root, POINTER_DIR, sid)
    try:
        with open(pointer, encoding="utf-8") as f:
            target = f.read().strip()
        if os.path.basename(target) == NAME and os.path.isfile(target):
            os.utime(pointer)  # a session that is still in use keeps its pointer
            return target
    except OSError:
        pass
    return None


def sweep_pointers(root, now):
    """Remove pointer files older than POINTER_DAYS, file by file."""
    if not root:
        return
    folder = os.path.join(root, POINTER_DIR)
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        path = os.path.join(folder, name)
        try:
            if os.path.isfile(path) and now - os.path.getmtime(path) > POINTER_DAYS * 86400:
                os.remove(path)
        except OSError:
            pass


def label_of(path, root):
    """A plan's label: its path from the plans root when it lies under it, else its absolute path; forward slashes."""
    if root:
        try:
            rel = os.path.relpath(os.path.realpath(path), os.path.realpath(root))
        except ValueError:  # another drive
            rel = None
        if rel is not None and rel != ".." and not rel.startswith(".." + os.sep) and not os.path.isabs(rel):
            return rel.replace(os.sep, "/")
    return os.path.abspath(path).replace(os.sep, "/")


def in_project(root, cwd):
    """Whether `cwd` is inside a git project other than the plans root itself."""
    top = project_top(cwd) if cwd else None
    return top is not None and not (root and same_path(top, root))


def main_plan(root, session_id, cwd):
    """The plan file the hook prints in full: the session's pointer (with a plans root), else the project of cwd, else
    the root's own plan when cwd is the root, when that file exists."""
    found = read_pointer(root, session_id)
    if found:
        return found
    if not cwd:
        return None
    base = project_top(cwd) or os.path.abspath(cwd)
    candidate = root_plan(root) if root and same_path(base, root) else os.path.join(base, "docs", NAME)
    return candidate if os.path.isfile(candidate) else None


def index_line(path, root, now):
    label = label_of(path, root)
    try:
        age = max(int((now - os.path.getmtime(path)) // 86400), 0)
        with open(path, encoding="utf-8") as f:
            plan = json.load(f)
        stages = plan["stages"]
        nxt = next((s for s in stages if s["status"] == "in_progress"), None)
        word = "now"
        if not nxt:
            nxt, word = next((s for s in stages if s["status"] == "pending"), None), "next"
        where = f"{word}: {nxt['id']}. {clip(nxt['title'], 60)}" if nxt else "every stage closed"
        done = sum(1 for s in stages if s["status"] == "done")
        return f"  {label} | {' '.join(str(plan['goal']).split())[:80]} | {done}/{len(stages)} done | {where} | {age}d"
    except (OSError, ValueError, KeyError, TypeError) as error:
        return f"  {label} | unreadable: {clip(error, 60)}"


def newest_first(paths):
    def mtime(p):
        try:
            return -os.path.getmtime(p)
        except OSError:
            return 0
    return sorted(paths, key=lambda p: (mtime(p), p))


def hook_text(root, source, session_id, cwd, now=None, cap=CAP):
    """What the SessionStart hook prints within `cap` characters: the main plan in full and, with a plans root and a
    session that is not inside one project, every other plan as an index line."""
    now = time.time() if now is None else now
    limit = cap - 1  # print adds a newline, and the cap counts it
    project = in_project(root, cwd)
    header = HEADER_COMPACT if source == "compact" else HEADER_PROJECT if (project or not root) else HEADER_INDEX
    main = main_plan(root, session_id, cwd)
    real_main = os.path.realpath(main) if main else None
    others = [] if (project or not root) else \
        [p for p in newest_first(active_plans(root)) if os.path.realpath(p) != real_main]
    if not main and not others:
        return ""
    index = [index_line(p, root, now) for p in others]
    plan, error = None, ""
    if main:
        try:
            with open(main, encoding="utf-8") as f:
                plan = json.load(f)
            plan["stages"]
        except (OSError, ValueError, KeyError, TypeError) as caught:
            plan, error = None, f"ACTIVE PLAN {label_of(main, root)} unreadable: {caught}"
    label = label_of(main, root) if main else ""
    in_progress = next((s for s in plan["stages"] if s["status"] == "in_progress"), None) if plan else None
    note_count = len(in_progress.get("notes", [])) if in_progress else 0

    def assemble(main_text, shown):
        parts = [header]
        if main_text:
            parts.append(main_text)
        if index:
            parts.append("Other active plans (index only; `work-plan show --where <project folder>` reads one):")
            parts.extend(index[:shown])
            if shown < len(index):
                parts.append(f"  and {len(index) - shown} more plans (see the ACTIVE-PLAN.json files under the plans root)")
        return "\n".join(parts)

    def main_text(drop, short=False, fold=False):
        return render(plan, label, brief=True, drop_notes=drop, short=short, fold=fold) if plan else error

    text = assemble(main_text(0), len(index))
    fold = len(text) > limit  # the older closed stages fold into one line of ids first: the open ones matter most
    if fold:
        text = assemble(main_text(0, fold=True), len(index))
    for drop in range(1, note_count + 1):  # then the oldest notes of the stage in progress
        if len(text) <= limit:
            break
        text = assemble(main_text(drop, fold=fold), len(index))
    shown = len(index)
    while len(text) > limit and shown > 0:  # then the index lines, newest plans kept
        shown -= 1
        text = assemble(main_text(note_count, fold=fold), shown)
    if len(text) > limit and plan:  # a plan too big alone: its goal and done-when texts are clipped
        text = assemble(main_text(note_count, short=True, fold=True), shown)
    if len(text) > limit:  # last resort: the cap holds even for a plan nothing can shorten
        mark = "\n(cut at the output limit; read the plan file)"
        text = text[:limit - len(mark)] + mark
    assert len(text) + 1 <= cap, f"hook output is {len(text) + 1} characters, over {cap}"
    return text


def read_hook_input():
    """The JSON Claude Code sends on stdin; {} when there is none or it is not an object."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        data = json.loads(sys.stdin.read() or "null")
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def trim_tail(lines, limit=TAIL_CHARS):
    """The output tail as one text of at most `limit` characters, keeping its end (where a failure shows)."""
    text = "\n".join(lines)
    return text if len(text) <= limit else "..." + text[-(limit - 3):]


def bundle_proof(path, words, timeout):
    """Run `words` through verify_run.py from the plan's project folder. Returns (verified dict, proof text) when it
    passed; exits with the reason, and the tail of the output, when it did not."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import verify_run
    folder = os.path.dirname(os.path.dirname(path))
    try:
        result = verify_run.run_command(words, folder, timeout)
    except verify_run.Refused as error:
        sys.exit(f"work_plan: the command was refused, the stage stays open: {error}")
    except ValueError as error:
        sys.exit(f"work_plan: {error}")
    if not verify_run.passed(result):
        how = f"timed out after {timeout:g} s" if result["timed_out"] else f"exit {result['exit']}"
        tail = "\n".join(result["tail"])
        sys.exit(f"work_plan: the command failed ({how}), the stage stays open:\n{tail}")
    verified = {"command": result["command"], "exit": result["exit"], "duration_s": result["duration_s"],
                "sha256": result["sha256"], "tail": trim_tail(result["tail"]), "time": result["time"]}
    return verified, f"verified: {result['command']} exit 0 in {result['duration_s']:.1f} s"


def split_command(argv):
    """(options, the command after the first `--`) when `--bundle` comes before that `--`, so the command's own options
    never reach argparse; any other `--` stays where it is (`note 1 -- "text"` keeps working)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" in argv:
        i = argv.index("--")
        if "--bundle" in argv[:i]:
            return argv[:i], argv[i + 1:]
    return argv, []


def main(argv=None, root=AUTO):
    root = plans_root() if root is AUTO else root
    argv, words = split_command(argv)
    ap = argparse.ArgumentParser(prog="work-plan")
    ap.add_argument("command", choices=["new", "add", "start", "note", "done", "drop", "pause", "show", "finish", "hook"])
    ap.add_argument("n", nargs="?", type=int)
    ap.add_argument("text", nargs="?")
    ap.add_argument("--where")
    ap.add_argument("--goal")
    ap.add_argument("--title")
    ap.add_argument("--done-when")
    ap.add_argument("--result")
    ap.add_argument("--proof")
    ap.add_argument("--reason")
    ap.add_argument("--bundle", action="store_true")
    ap.add_argument("--timeout", type=float, default=BUNDLE_TIMEOUT)
    a = ap.parse_intermixed_args(argv)
    if a.bundle and a.command != "done":
        sys.exit("work_plan: --bundle belongs to done")  # `note 2 --where X "text"` and `note 2 "text" --where X` both work

    if a.command == "hook":
        data = read_hook_input()
        if os.environ.get(SILENT_ENV) or os.environ.get(OLD_SILENT_ENV) or not module_enabled("proof_plans"):
            return 0
        sweep_pointers(root, time.time())
        cwd = data.get("cwd") if isinstance(data.get("cwd"), str) and data.get("cwd") else os.getcwd()
        text = hook_text(root, data.get("source"), data.get("session_id"), cwd)
        if text:
            print(text)
        return 0

    path = plan_path(a.where, root)
    if a.command == "done" and a.bundle:
        return done_bundle(a, path, root, words)
    if a.command == "show":  # show only reads: it takes no lock, never saves the plan nor moves the session's pointer
        print(render(load(path), label_of(path, root)))
        return 0
    with plan_lock(path, create=a.command == "new"):  # one load-modify-save at a time per plan
        return change_plan(a, path, root)


def changed(plan, path, root, touched):
    """Save the plan, move the session's pointer and print what the command touched."""
    save(path, plan)
    write_pointer(root, path)
    print(render_change(plan, label_of(path, root), touched))
    return 0


def done_bundle(a, path, root, words):
    """done --bundle: check under the lock that the stage can close, run the command with no lock held (it may take
    up to --timeout seconds, and other commands must keep working meanwhile), then reload the plan under the lock and
    close the stage on that fresh copy, so a note or a stage added meanwhile is kept. A stage that is no longer in
    progress by then (paused, dropped, closed by another command) is not closed."""
    if not a.result or a.proof or not words:
        sys.exit("work_plan: done --bundle needs --result and the command after `--`, and no --proof")
    with plan_lock(path):
        s = stage(load(path), a.n)
        if s["status"] != "in_progress":
            sys.exit(f"work_plan: stage {a.n} is {s['status']}; start it first")
    verified, proof = bundle_proof(path, words, a.timeout)
    with plan_lock(path):
        plan = load(path)
        s = stage(plan, a.n)
        if s["status"] != "in_progress":
            sys.exit(f"work_plan: the command passed, but stage {a.n} is {s['status']} now (changed while it ran); "
                     "the stage was not closed by it")
        s.update(status="done", result=a.result, proof=proof, verified=verified, notes=[])
        return changed(plan, path, root, a.n)


def change_plan(a, path, root):
    if a.command == "new":
        if os.path.exists(path):
            sys.exit(f"work_plan: {path} already holds an active plan; add stages to it or finish it first")
        if not a.goal:
            sys.exit("work_plan: new needs --goal")
        save(path, {"goal": a.goal, "created": datetime.date.today().isoformat(), "stages": []})
        write_pointer(root, path)
        print(f"new plan: {path}")
        return 0

    plan = load(path)
    label = label_of(path, root)
    if a.command == "add":
        if not (a.title and a.done_when):
            sys.exit("work_plan: add needs --title and --done-when")
        n = max([s["id"] for s in plan["stages"]] or [0]) + 1
        plan["stages"].append({"id": n, "title": a.title, "done_when": a.done_when, "status": "pending", "notes": []})
    elif a.command == "start":
        s = stage(plan, a.n)
        if s["status"] not in OPEN:
            sys.exit(f"work_plan: stage {a.n} is {s['status']}")
        busy = [x["id"] for x in plan["stages"] if x["status"] == "in_progress" and x["id"] != a.n]
        if busy:
            sys.exit(f"work_plan: stage {busy[0]} is in progress; one stage at a time")
        s["status"] = "in_progress"
    elif a.command == "note":
        s = stage(plan, a.n)
        if s["status"] not in OPEN or not a.text:
            sys.exit("work_plan: note needs an open stage and its text")
        s.setdefault("notes", []).append(a.text)
    elif a.command == "done":  # a proof that is not a command: shown as [unverified]; --bundle is the verified close
        s = stage(plan, a.n)
        if s["status"] != "in_progress":
            sys.exit(f"work_plan: stage {a.n} is {s['status']}; start it first")
        if not (a.result and a.proof):
            sys.exit("work_plan: done needs --result and --proof (or --result and --bundle -- <command>)")
        s.update(status="done", result=a.result, proof=a.proof, notes=[])
    elif a.command == "pause":
        s = stage(plan, a.n)
        if s["status"] != "in_progress":
            sys.exit(f"work_plan: stage {a.n} is {s['status']}; only a stage in progress is paused")
        if not a.reason:
            sys.exit("work_plan: pause needs --reason (what the stage waits on)")
        s["status"] = "pending"
        s.setdefault("notes", []).append(f"Paused: {a.reason}")
    elif a.command == "drop":
        s = stage(plan, a.n)
        if s["status"] not in OPEN or not a.reason:
            sys.exit("work_plan: drop needs an open stage and --reason")
        s.update(status="dropped", result=a.reason, notes=[])
    elif a.command == "finish":
        still = [s["id"] for s in plan["stages"] if s["status"] in OPEN]
        if still or not plan["stages"]:
            sys.exit(f"work_plan: stages still open: {still}" if still else "work_plan: the plan has no stages")
        os.remove(path)
        print(f"finished and removed: {label}")
        return 0
    touched = max(s["id"] for s in plan["stages"]) if a.command == "add" else a.n
    return changed(plan, path, root, touched)


if __name__ == "__main__":
    sys.exit(main())
