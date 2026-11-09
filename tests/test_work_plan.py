#!/usr/bin/env python
"""Tests for work_plan.py. Run: python -B test_work_plan.py"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

TESTS = os.path.dirname(os.path.abspath(__file__))
HERE = os.path.join(os.path.dirname(TESTS), "plugins", "workproof", "scripts")  # the engines under test
sys.path.insert(0, HERE)
import work_plan as wp  # noqa: E402

PLUGIN = os.path.dirname(HERE)
PY = sys.executable
CLEAN_ENV = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", wp.ROOT_ENV, wp.ROOT_OPTION_ENV, wp.SILENT_ENV)


def isolate(case):
    """Patch os.environ for one test and drop every variable the plugin reads."""
    env = mock.patch.dict(os.environ)
    env.start()
    case.addCleanup(env.stop)
    for name in CLEAN_ENV:
        os.environ.pop(name, None)


def make_repo(path, as_file=False):
    """A folder that counts as a git top-level (a .git folder, or a .git file as in a worktree)."""
    os.makedirs(path, exist_ok=True)
    if as_file:
        with open(os.path.join(path, ".git"), "w") as f:
            f.write("gitdir: elsewhere\n")
    else:
        os.makedirs(os.path.join(path, ".git"), exist_ok=True)
    return path


@contextlib.contextmanager
def chdir(path):
    here = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(here)  # Windows keeps a working folder locked


def with_stdin(text, argv, **kwargs):
    out = io.StringIO()
    saved, sys.stdin = sys.stdin, io.StringIO(text)
    try:
        with contextlib.redirect_stdout(out):
            wp.main(argv, **kwargs)
    finally:
        sys.stdin = saved
    return out.getvalue()


class Plan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        self.project = make_repo(os.path.join(self.root, "group", "sub", "shop"))
        self.path = os.path.join(self.project, "docs", wp.NAME)
        isolate(self)

    def tearDown(self):
        self.tmp.cleanup()

    def run_plan(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            wp.main(list(args) + ["--where", self.project], root=self.root)
        return out.getvalue()

    def fails(self, *args):
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stdout(io.StringIO()):
            wp.main(list(args) + ["--where", self.project], root=self.root)
        return str(caught.exception.code)

    def plan(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def test_stages_run_one_at_a_time_and_a_done_stage_keeps_one_line(self):
        self.run_plan("new", "--goal", "build the shop")
        self.run_plan("add", "--title", "model", "--done-when", "tests green")
        self.run_plan("add", "--title", "screen", "--done-when", "capture read")
        self.run_plan("start", "1")
        self.assertIn("one stage at a time", self.fails("start", "2"))
        self.run_plan("note", "1", "prices live in the tuning file")
        self.assertIn("prices live in the tuning file", self.run_plan("show"))
        self.assertIn("--result and --proof", self.fails("done", "1", "--result", "ok"))
        out = self.run_plan("done", "1", "--result", "model built", "--proof", "ShopTests 6 passed")
        stage = self.plan()["stages"][0]
        self.assertEqual((stage["status"], stage["notes"]), ("done", []))
        self.assertIn("[done] 1. model [unverified]: model built (proof: ShopTests 6 passed)", out)
        self.assertIn("next: stage 2, screen", out)

    def test_a_stage_that_waits_is_paused_with_its_reason_and_another_starts(self):
        self.run_plan("new", "--goal", "g")
        self.run_plan("add", "--title", "install", "--done-when", "on the device")
        self.run_plan("add", "--title", "audit", "--done-when", "green")
        self.assertIn("in progress", self.fails("pause", "1", "--reason", "waits on a device"))
        self.run_plan("start", "1")
        self.assertIn("--reason", self.fails("pause", "1"))
        self.run_plan("pause", "1", "--reason", "waits on a device")
        first = self.plan()["stages"][0]
        self.assertEqual(first["status"], "pending")
        self.assertEqual(first["notes"][-1], "Paused: waits on a device")
        self.run_plan("start", "2")
        self.assertEqual(self.plan()["stages"][1]["status"], "in_progress")

    def test_a_stage_is_never_removed_and_finish_needs_every_stage_closed(self):
        self.run_plan("new", "--goal", "g")
        self.run_plan("add", "--title", "a", "--done-when", "x")
        self.assertIn("still open: [1]", self.fails("finish"))
        self.assertIn("--reason", self.fails("drop", "1"))
        self.run_plan("drop", "1", "--reason", "no longer needed")
        self.assertEqual(len(self.plan()["stages"]), 1)
        self.run_plan("finish")
        self.assertFalse(os.path.exists(self.path))

    def test_a_note_reads_its_text_after_the_options_too(self):
        self.run_plan("new", "--goal", "g")
        self.run_plan("add", "--title", "a", "--done-when", "x")
        self.run_plan("start", "1")
        with contextlib.redirect_stdout(io.StringIO()):
            wp.main(["note", "1", "--where", self.project, "waiting on the build"], root=self.root)
        self.assertEqual(self.plan()["stages"][0]["notes"], ["waiting on the build"])

    def test_a_change_prints_the_touched_stage_and_the_open_ones_not_the_history(self):
        self.run_plan("new", "--goal", "g")
        for i in range(1, 8):
            self.run_plan("add", "--title", f"stage {i}", "--done-when", "x")
        for i in range(1, 6):
            self.run_plan("start", str(i))
            self.run_plan("done", str(i), "--result", f"RESULT-{i}", "--proof", "p")
        self.run_plan("start", "6")
        out = self.run_plan("note", "6", "NOTE-six")
        self.assertTrue(out.startswith("ACTIVE PLAN group/sub/shop/docs/ACTIVE-PLAN.json: 5 done, 0 dropped, 2 open"))
        self.assertIn("  [now] 6. stage 6 | done when: x\n        - NOTE-six\n", out)
        self.assertIn("  [todo] 7. stage 7 | done when: x\n", out)
        self.assertNotIn("RESULT-", out)
        self.assertNotIn("[done]", out)
        self.assertTrue(out.rstrip("\n").endswith("next: stage 6, stage 6"))
        added = self.run_plan("add", "--title", "stage 8", "--done-when", "y")
        self.assertIn("  [todo] 8. stage 8 | done when: y", added)
        self.assertNotIn("NOTE-six", added, "an open stage the command did not touch shows without its notes")
        self.assertIn("[done] 1. stage 1 [unverified]: RESULT-1 (proof: p)", self.run_plan("show"))

    def test_the_plan_file_is_written_with_lf_line_ends_on_every_platform(self):
        self.run_plan("new", "--goal", "build the shop")
        self.run_plan("add", "--title", "model", "--done-when", "tests green")
        with open(self.path, "rb") as f:
            raw = f.read()
        self.assertNotIn(b"\r\n", raw)
        self.assertTrue(raw.endswith(b"}\n"))

    def test_the_plan_file_keeps_the_documented_schema(self):
        self.run_plan("new", "--goal", "g")
        self.run_plan("add", "--title", "a", "--done-when", "x")
        self.run_plan("start", "1")
        self.run_plan("note", "1", "n")
        data = self.plan()
        self.assertEqual(sorted(data), ["created", "goal", "stages"])
        self.assertEqual(sorted(data["stages"][0]), ["done_when", "id", "notes", "status", "title"])
        self.run_plan("done", "1", "--result", "r", "--proof", "p")
        self.assertEqual(sorted(self.plan()["stages"][0]),
                         ["done_when", "id", "notes", "proof", "result", "status", "title"])

    def test_id_ranges_join_consecutive_ids(self):
        self.assertEqual(wp.id_ranges([7, 1, 2, 3, 5, 8]), "1-3, 5, 7-8")
        self.assertEqual(wp.id_ranges([4]), "4")

    def test_a_second_plan_for_the_same_project_is_refused(self):
        self.run_plan("new", "--goal", "g")
        self.assertIn("already holds", self.fails("new", "--goal", "h"))

    def test_the_hook_prints_the_plan_of_the_cwd_project_with_its_open_stage_notes(self):
        self.run_plan("new", "--goal", "ship it")
        self.run_plan("add", "--title", "audit", "--done-when", "seal")
        self.run_plan("start", "1")
        self.run_plan("note", "1", "rule 2.1 pending")
        text = with_stdin(json.dumps({"source": "compact", "cwd": self.project}), ["hook"], root=self.root)
        self.assertIn("ACTIVE PLAN group/sub/shop/docs/ACTIVE-PLAN.json", text)
        self.assertIn("[now] 1. audit", text)
        self.assertIn("rule 2.1 pending", text)

    def test_the_hook_is_silent_when_the_silent_variable_is_set_even_with_a_plan(self):
        self.run_plan("new", "--goal", "ship it")
        self.run_plan("add", "--title", "audit", "--done-when", "seal")
        os.environ[wp.SILENT_ENV] = "1"
        self.assertEqual(with_stdin(json.dumps({"source": "startup", "cwd": self.project}), ["hook"], root=self.root), "")

    def test_the_hook_is_silent_with_no_plan(self):
        empty = os.path.join(self.root, "empty")
        os.makedirs(empty)
        with chdir(empty):
            self.assertEqual(with_stdin("", ["hook"], root=self.root), "")
            self.assertEqual(with_stdin("", ["hook"], root=None), "")


class Discovery(unittest.TestCase):
    """The project is the git top-level of the folder; outside git, the folder itself."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.realpath(self.tmp.name)
        isolate(self)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_subfolder_of_a_repository_finds_the_top_level_plan(self):
        repo = make_repo(os.path.join(self.base, "my repo"))
        sub = os.path.join(repo, "src", "deep")
        os.makedirs(sub)
        expected = os.path.join(repo, "docs", wp.NAME)
        with chdir(sub):
            self.assertEqual(os.path.realpath(wp.plan_path(None, None)), os.path.realpath(expected))
        self.assertEqual(wp.plan_path(sub, None), expected)
        self.assertEqual(wp.plan_path(repo, None), expected)

    def test_a_git_file_as_in_a_worktree_marks_a_top_level_too(self):
        repo = make_repo(os.path.join(self.base, "wt"), as_file=True)
        sub = os.path.join(repo, "a")
        os.makedirs(sub)
        self.assertEqual(wp.plan_path(sub, None), os.path.join(repo, "docs", wp.NAME))

    def test_the_nearest_repository_wins_over_an_outer_one(self):
        outer = make_repo(os.path.join(self.base, "outer"))
        inner = make_repo(os.path.join(outer, "vendor", "inner"))
        self.assertEqual(wp.plan_path(inner, None), os.path.join(inner, "docs", wp.NAME))

    def test_outside_git_the_folder_itself_is_the_project(self):
        plain = os.path.join(self.base, "plain", "folder")
        os.makedirs(plain)
        self.assertEqual(wp.plan_path(plain, None), os.path.join(plain, "docs", wp.NAME))
        with chdir(plain):
            self.assertEqual(os.path.realpath(wp.plan_path(None, None)),
                             os.path.realpath(os.path.join(plain, "docs", wp.NAME)))

    def test_where_must_be_a_folder(self):
        with self.assertRaises(SystemExit) as caught:
            wp.plan_path(os.path.join(self.base, "missing"), None)
        self.assertIn("is not a folder", str(caught.exception.code))

    def test_the_cli_run_from_a_subfolder_writes_the_top_level_plan(self):
        repo = make_repo(os.path.join(self.base, "repo"))
        sub = os.path.join(repo, "src")
        os.makedirs(sub)
        script = os.path.join(HERE, "work_plan.py")
        p = subprocess.run([PY, "-B", script, "new", "--goal", "from a subfolder"], cwd=sub, capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(os.path.isfile(os.path.join(repo, "docs", wp.NAME)))
        self.assertFalse(os.path.exists(os.path.join(sub, "docs")))
        p = subprocess.run([PY, "-B", script, "show", "--where", sub], cwd=self.base, capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("goal: from a subfolder", p.stdout)

    def test_outside_git_the_cli_writes_into_the_current_folder(self):
        plain = os.path.join(self.base, "plain")
        os.makedirs(plain)
        p = subprocess.run([PY, "-B", os.path.join(HERE, "work_plan.py"), "new", "--goal", "g"], cwd=plain,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(os.path.isfile(os.path.join(plain, "docs", wp.NAME)))

    def test_no_projects_root_is_assumed(self):
        self.assertIsNone(wp.plans_root())
        self.assertFalse(hasattr(wp, "ROOT"))


class PlansRoot(unittest.TestCase):
    """The plans root: PROOF_PLANS_ROOT, else the plugin option; it adds the index, labels and the pointer."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        isolate(self)
        self.a = make_repo(os.path.join(self.root, "alpha"))
        self.b = make_repo(os.path.join(self.root, "area", "kind", "beta"))
        for project, name in ((self.a, "alpha"), (self.b, "beta")):
            write_json(os.path.join(project, "docs", wp.NAME), f"Goal of {name}",
                       [stage(1, "done", f"{name} first"), stage(2, "in_progress", f"{name} second")])

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_environment_variable_then_the_plugin_option_then_nothing(self):
        self.assertIsNone(wp.plans_root())
        os.environ[wp.ROOT_OPTION_ENV] = self.root
        self.assertEqual(wp.plans_root(), os.path.normpath(self.root))
        other = os.path.join(self.root, "area")
        os.environ[wp.ROOT_ENV] = other
        self.assertEqual(wp.plans_root(), os.path.normpath(other), "the CLI variable wins over the option")
        os.environ[wp.ROOT_ENV] = os.path.join(self.root, "missing")
        os.environ.pop(wp.ROOT_OPTION_ENV)
        self.assertIsNone(wp.plans_root(), "a folder that does not exist counts as unset")
        os.environ[wp.ROOT_ENV] = "   "
        self.assertIsNone(wp.plans_root())

    def test_with_a_root_from_the_option_a_project_session_sees_its_plan_alone(self):
        os.environ[wp.ROOT_OPTION_ENV] = self.root
        text = with_stdin(json.dumps({"source": "startup", "cwd": self.a, "session_id": "s"}), ["hook"])
        self.assertIn("[now] 2. alpha second", text)
        self.assertIn("ACTIVE PLAN alpha/docs/ACTIVE-PLAN.json", text)
        self.assertNotIn("beta", text)
        self.assertNotIn("Other active plans", text)

    def test_with_a_root_a_session_outside_any_project_gets_the_index(self):
        os.environ[wp.ROOT_ENV] = self.root
        text = with_stdin(json.dumps({"source": "startup", "cwd": self.root, "session_id": "s"}), ["hook"])
        self.assertTrue(text.startswith("Active plans under the plans root (skill workproof:working-in-stages)"))
        self.assertIn("Other active plans", text)
        self.assertIn("  alpha/docs/ACTIVE-PLAN.json | Goal of alpha | 1/2 done | now: 2. alpha second | 0d", text)
        self.assertIn("  area/kind/beta/docs/ACTIVE-PLAN.json | Goal of beta | 1/2 done | now: 2. beta second | 0d", text)
        self.assertNotIn("ACTIVE PLAN ", text)

    def test_the_root_folder_has_its_own_plan_under_dot_claude(self):
        os.environ[wp.ROOT_ENV] = self.root
        with contextlib.redirect_stdout(io.StringIO()):
            wp.main(["new", "--goal", "root work", "--where", self.root])
        self.assertTrue(os.path.isfile(os.path.join(self.root, ".claude", wp.NAME)))
        text = with_stdin(json.dumps({"source": "startup", "cwd": self.root, "session_id": "s"}), ["hook"])
        self.assertIn("ACTIVE PLAN .claude/ACTIVE-PLAN.json", text)
        self.assertIn("  alpha/docs/ACTIVE-PLAN.json | ", text)

    def test_without_a_root_there_is_no_index_and_no_pointer(self):
        os.environ[wp.ROOT_ENV] = ""
        for cwd in (self.root, self.a):
            text = with_stdin(json.dumps({"source": "startup", "cwd": cwd, "session_id": "s"}), ["hook"])
            self.assertNotIn("Other active plans", text)
            self.assertNotIn("beta", text if cwd == self.a else "")
        # the plan of the folder is printed, the sibling plans are not
        text = with_stdin(json.dumps({"source": "startup", "cwd": self.a}), ["hook"])
        self.assertTrue(text.startswith("Active plan of this project (skill workproof:working-in-stages)"))
        self.assertIn("ACTIVE PLAN " + self.a.replace(os.sep, "/") + "/docs/ACTIVE-PLAN.json", text)
        os.environ["CLAUDE_CODE_SESSION_ID"] = "sess-1"
        with contextlib.redirect_stdout(io.StringIO()):
            wp.main(["note", "2", "x", "--where", self.a])
        self.assertFalse(os.path.exists(os.path.join(self.root, ".claude")))

    def test_the_hook_prints_nothing_for_a_folder_with_no_plan_and_no_root(self):
        quiet = make_repo(os.path.join(self.root, "quiet"))
        self.assertEqual(with_stdin(json.dumps({"cwd": quiet}), ["hook"]), "")

    def test_a_project_label_outside_the_root_is_its_absolute_path(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            path = os.path.join(os.path.realpath(elsewhere), "docs", wp.NAME)
            self.assertEqual(wp.label_of(path, self.root), path.replace(os.sep, "/"))
            self.assertEqual(wp.label_of(path, None), path.replace(os.sep, "/"))
        self.assertEqual(wp.label_of(os.path.join(self.a, "docs", wp.NAME), self.root), "alpha/docs/ACTIVE-PLAN.json")


def write_json(path, goal, stages):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"goal": goal, "created": "2026-10-01", "stages": stages}, f, indent=1)


def stage(n, status, title, done_when="the screen is captured and read", notes=()):
    one = {"id": n, "title": title, "done_when": done_when, "status": status, "notes": list(notes)}
    if status == "done":
        one.update(result=f"RESULT-{title} built and reviewed", proof=f"PROOF-{title} 12 tests passed")
    return one


def small_plan(root, rel, name, notes=("one short note",)):
    """A plan of three stages (done, now, pending) in a project folder with a .git."""
    project = make_repo(os.path.join(root, rel))
    path = os.path.join(project, "docs", wp.NAME)
    write_json(path, f"Build {name}: the order, quoted at some length so the index has to cut it short",
               [stage(1, "done", f"{name} first"), stage(2, "in_progress", f"{name} second", notes=notes),
                stage(3, "pending", f"{name} third")])
    return path


class Hook(unittest.TestCase):
    """The SessionStart hook with a plans root: the session's plan in full, the others as index lines, under the cap."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        isolate(self)
        self.others = []
        for i in range(8):
            self.others.append(small_plan(self.root, f"apps/Tool{i}", f"Tool{i}"))
        for i in range(9):
            self.others.append(small_plan(self.root, f"games/Svc{i}", f"Svc{i}"))
        self.rootplan = os.path.join(self.root, ".claude", wp.NAME)
        write_json(self.rootplan, "Rework the structure", [stage(1, "in_progress", "ROOTSTAGE one")])
        self.big_dir = make_repo(os.path.join(self.root, "systems", "docker", "Big"))
        self.big = os.path.join(self.big_dir, "docs", wp.NAME)
        self.notes = [f"NOTE-{i:02d} " + "decision, files touched and the failing test " * 8 for i in range(12)]
        stages = [stage(i, "done", f"Big done {i}", done_when="d" * 150) for i in range(1, 54)]
        stages.append(stage(54, "in_progress", "Big NOWSTAGE", done_when="the dashboard test is green", notes=self.notes))
        stages += [stage(i, "pending", f"Big todo {i}", done_when="p" * 120) for i in range(55, 78)]
        write_json(self.big, "The Big order, quoted in full " + "g" * 600, stages)

    def tearDown(self):
        self.tmp.cleanup()

    def hook(self, payload=None, raw=None):
        text = raw if raw is not None else ("" if payload is None else json.dumps(payload))
        return with_stdin(text, ["hook"], root=self.root)

    def lines(self, text):
        return text.split("\n")

    def rel(self, path):
        return os.path.relpath(path, self.root).replace(os.sep, "/")

    def test_a_project_session_sees_its_own_plan_alone(self):
        text = self.hook({"session_id": "s1", "source": "startup", "cwd": self.big_dir})
        self.assertTrue(text.startswith("Active plan of this project (skill workproof:working-in-stages)"))
        self.assertIn("[now] 54. Big NOWSTAGE", text)
        self.assertNotIn("Other active plans", text)
        for path in self.others + [self.rootplan]:
            self.assertNotIn(self.rel(path), text)

    def test_a_project_without_a_plan_is_silent_even_with_other_plans(self):
        empty = make_repo(os.path.join(self.root, "apps", "Quiet"))
        self.assertEqual(self.hook({"session_id": "s1", "source": "startup", "cwd": empty}), "")

    def test_a_root_session_stays_under_the_cap_and_indexes_every_other_plan(self):
        text = self.hook({"session_id": "s1", "source": "startup", "cwd": self.root})
        self.assertLessEqual(len(text), 9500)
        self.assertIn("[now] 1. ROOTSTAGE one", text)
        for path in self.others + [self.big]:
            self.assertTrue(any(line.startswith(f"  {self.rel(path)} | ") for line in self.lines(text)), path)
        self.assertNotIn("more plans", text)

    def test_the_oldest_notes_of_the_now_stage_go_first_and_the_count_is_said(self):
        self.notes = [f"NOTE-{i:02d} " + "decision, files touched and the failing test " * 8 for i in range(40)]
        with open(self.big, encoding="utf-8") as f:
            data = json.load(f)
        next(s for s in data["stages"] if s["status"] == "in_progress")["notes"] = self.notes
        write_json(self.big, data["goal"], data["stages"])
        uncapped = wp.hook_text(self.root, "startup", "", self.big_dir, cap=10 ** 9)
        self.assertGreater(len(uncapped), 9500, "the fixture must be big enough to need the cap")
        text = self.hook({"session_id": "s1", "source": "startup", "cwd": self.big_dir})
        self.assertLessEqual(len(text), 9500)
        kept = [n for n in self.notes if n in text]
        self.assertTrue(0 < len(kept) < len(self.notes))
        self.assertEqual(kept, self.notes[-len(kept):], "the newest notes are the ones kept")
        self.assertIn(f"({len(self.notes) - len(kept)} older notes left out", text)

    def test_a_plan_too_big_alone_loses_index_lines_never_its_stage_list(self):
        with open(self.big, encoding="utf-8") as f:  # the root session's plan is as big as Big, with long done-when texts
            data = json.load(f)
        for s in data["stages"]:
            if s["status"] == "pending":
                s["done_when"] = "q" * 215
        write_json(self.rootplan, data["goal"], data["stages"])
        for n, path in enumerate(self.others + [self.big]):  # distinct ages, Big the oldest
            stamp = time.time() - (n + 1) * 3600
            os.utime(path, (stamp, stamp))
        text = self.hook({"session_id": "s1", "source": "compact", "cwd": self.root})
        self.assertLessEqual(len(text), 9500)
        self.assertTrue(text.startswith("Context was compacted."))
        self.assertIn("[now] 54. Big NOWSTAGE", text)
        self.assertIn("  [closed] 1-48 (48 stages; `work-plan show` lists them)\n", text)
        for i in range(49, 78):
            self.assertRegex(text, rf"\[(done|now|todo)\] {i}\. ")
        shown = sum(1 for line in self.lines(text) if "ACTIVE-PLAN.json | " in line)
        hidden = int(text.split("  and ")[1].split(" more plans")[0])
        self.assertGreater(hidden, 0)
        self.assertEqual(shown + hidden, 18)
        kept = [l.split(" | ")[0].strip() for l in self.lines(text) if "ACTIVE-PLAN.json | " in l]
        by_age = sorted([self.rel(p) for p in self.others + [self.big]],
                        key=lambda r: -os.path.getmtime(os.path.join(self.root, r)))
        self.assertEqual(kept, by_age[:len(kept)], "the plans touched most recently are the ones kept")

    def test_a_plan_nothing_can_shorten_still_meets_the_cap(self):
        write_json(self.big, "g", [stage(i, "done", "t" * 400) for i in range(1, 40)])
        text = self.hook({"session_id": "s1", "source": "compact", "cwd": self.big_dir})
        self.assertLessEqual(len(text), 9500)
        self.assertTrue(text.startswith("Context was compacted."))

    def test_a_long_closed_history_folds_so_every_open_stage_shows(self):
        stages = [stage(i, "done", f"CLOSED-{i:02d} " + "t" * 300) for i in range(1, 61)]
        stages += [stage(i, "pending", f"OPEN-{i} " + "o" * 60, done_when="w" * 200) for i in range(61, 64)]
        write_json(self.rootplan, "Rework the structure", stages)
        text = self.hook({"session_id": "s1", "source": "compact", "cwd": self.root})
        self.assertLessEqual(len(text), 9500)
        self.assertNotIn("cut at the output limit", text)
        for i in range(61, 64):
            self.assertIn(f"  [todo] {i}. OPEN-{i} ", text)
        self.assertIn("  [closed] 1-55 (55 stages; `work-plan show` lists them)\n", text)
        for i in range(56, 61):
            self.assertIn(f"  [done] {i}. CLOSED-{i} ", text)
        self.assertNotIn("CLOSED-55", text)
        self.assertIn("done when: " + "w" * 200 + "\n", text, "a folded text that fits is not clipped")

    def test_a_plan_that_fits_never_folds(self):
        write_json(self.big, "g", [stage(i, "done", f"short {i}") for i in range(1, 9)] + [stage(9, "pending", "next")])
        text = self.hook({"session_id": "s1", "source": "startup", "cwd": self.big_dir})
        self.assertNotIn("[closed]", text)
        self.assertIn("  [done] 1. short 1 [unverified]\n", text)

    def test_folding_comes_before_the_notes(self):
        stages = [stage(i, "done", f"Big done {i} " + "t" * 200) for i in range(1, 54)]
        stages.append(stage(54, "in_progress", "Big NOWSTAGE", notes=self.notes))
        write_json(self.big, "The Big order", stages)
        text = self.hook({"session_id": "s1", "source": "startup", "cwd": self.big_dir})
        self.assertIn("  [closed] 1-48 (48 stages", text)
        for note in self.notes:
            self.assertIn(note, text)

    def test_the_session_pointer_beats_the_cwd(self):
        pointer_dir = os.path.join(self.root, ".claude", "plan-sessions")
        os.makedirs(pointer_dir)
        with open(os.path.join(pointer_dir, "sess-1"), "w") as f:
            f.write(self.others[0] + "\n")  # Tool0
        svc = os.path.join(self.root, "games", "Svc3")
        text = self.hook({"session_id": "sess-1", "source": "resume", "cwd": svc})
        self.assertIn("[now] 2. Tool0 second", text)
        self.assertNotIn("[now] 2. Svc3 second", text)
        self.assertNotIn("Other active plans", text)  # a project session sees no other plan
        self.assertTrue(text.split("\n")[1].startswith("ACTIVE PLAN apps/Tool0/docs/ACTIVE-PLAN.json"))

    def test_without_a_pointer_the_project_holding_the_cwd_is_the_main_plan(self):
        sub = os.path.join(self.root, "games", "Svc3", "Sources", "Deep")
        os.makedirs(sub)
        text = self.hook({"session_id": "no-pointer", "source": "startup", "cwd": sub})
        self.assertIn("[now] 2. Svc3 second", text)
        self.assertNotIn("[now] 2. Svc4 second", text)
        other = self.hook({"session_id": "no-pointer", "source": "startup", "cwd": os.path.join(self.root, "games", "Svc4")})
        self.assertIn("[now] 2. Svc4 second", other)

    def test_a_pointer_to_a_removed_plan_falls_back_to_the_cwd(self):
        pointer_dir = os.path.join(self.root, ".claude", "plan-sessions")
        os.makedirs(pointer_dir)
        with open(os.path.join(pointer_dir, "gone"), "w") as f:
            f.write(os.path.join(self.root, "games", "Nope", "docs", wp.NAME) + "\n")
        text = self.hook({"session_id": "gone", "source": "startup", "cwd": os.path.join(self.root, "games", "Svc2")})
        self.assertIn("[now] 2. Svc2 second", text)

    def test_cwd_at_root_gives_the_root_plan_and_another_folder_gives_the_index_only(self):
        at_root = self.hook({"session_id": "s", "source": "startup", "cwd": self.root})
        self.assertIn("[now] 1. ROOTSTAGE one", at_root)
        elsewhere = self.hook({"session_id": "s", "source": "startup", "cwd": os.path.join(self.root, "Marketing")})
        self.assertNotIn("[now]", elsewhere)
        self.assertIn(f"  {self.rel(self.rootplan)} | ", elsewhere)

    def test_no_cwd_on_stdin_uses_the_process_folder(self):
        with chdir(os.path.join(self.root, "games", "Svc3")):
            for text in (self.hook(), self.hook(raw="not json at all"), self.hook(raw="[1, 2]"),
                         self.hook(raw='{"cwd": 5}')):
                self.assertIn("[now] 2. Svc3 second", text)
                self.assertNotIn("Other active plans", text)

    def test_the_index_line_says_goal_counts_stage_and_age(self):
        old = time.time() - 3 * 86400 - 600
        os.utime(self.others[0], (old, old))
        text = self.hook({"cwd": self.root})
        line = next(l for l in self.lines(text) if l.startswith("  apps/Tool0/docs/ACTIVE-PLAN.json | "))
        goal = "Build Tool0: the order, quoted at some length so the index has to cut it short"[:80]
        self.assertEqual(line, f"  apps/Tool0/docs/ACTIVE-PLAN.json | {goal} | 1/3 done | now: 2. Tool0 second | 3d")

    def test_compact_prints_the_paragraph_and_the_other_sources_do_not(self):
        compact = self.hook({"session_id": "s", "source": "compact", "cwd": self.root})
        self.assertTrue(compact.startswith("Context was compacted. Invoked skills came back only in part"))
        self.assertIn("then continue this plan from its now stage and its notes.", compact)
        for source in ("startup", "resume", "clear"):
            text = self.hook({"session_id": "s", "source": source, "cwd": self.root})
            self.assertTrue(text.startswith("Active plans under the plans root (skill workproof:working-in-stages). Continue"))
            self.assertNotIn("Context was compacted", text)

    def test_done_stages_are_one_line_with_a_title_and_nothing_more(self):
        text = self.hook({"session_id": "s", "source": "startup", "cwd": self.big_dir})
        self.assertIn("  [done] 53. Big done 53 [unverified]\n", text)
        self.assertNotIn("RESULT-", text)
        self.assertNotIn("PROOF-", text)
        self.assertNotIn("d" * 150, text)

    def test_pointer_files_older_than_14_days_are_removed_and_newer_kept(self):
        pointer_dir = os.path.join(self.root, ".claude", "plan-sessions")
        os.makedirs(pointer_dir)
        now = time.time()
        for name, days in (("old", 15), ("edge", 13), ("fresh", 0)):
            path = os.path.join(pointer_dir, name)
            with open(path, "w") as f:
                f.write(self.big + "\n")
            os.utime(path, (now - days * 86400, now - days * 86400))
        self.hook({"session_id": "x", "source": "startup", "cwd": self.root})
        self.assertEqual(sorted(os.listdir(pointer_dir)), ["edge", "fresh"])

    def test_every_plan_changing_command_writes_the_session_pointer(self):
        os.environ["CLAUDE_CODE_SESSION_ID"] = "11111111-2222-3333-4444-555555555555"
        project = make_repo(os.path.join(self.root, "games", "Fresh"))
        plan = os.path.join(project, "docs", wp.NAME)
        pointer = os.path.join(self.root, ".claude", "plan-sessions", os.environ["CLAUDE_CODE_SESSION_ID"])
        commands = [["new", "--goal", "g"], ["add", "--title", "a", "--done-when", "x"], ["start", "1"],
                    ["note", "1", "a note"], ["done", "1", "--result", "r", "--proof", "p"],
                    ["add", "--title", "b", "--done-when", "x"], ["drop", "2", "--reason", "no"]]
        for command in commands:
            if os.path.exists(pointer):
                os.remove(pointer)
            with contextlib.redirect_stdout(io.StringIO()):
                wp.main(command + ["--where", project], root=self.root)
            with open(pointer, encoding="utf-8") as f:
                self.assertEqual(f.read().strip(), plan, command)

    def test_show_is_read_only_and_never_moves_the_session_pointer(self):
        os.environ["CLAUDE_CODE_SESSION_ID"] = "sess-show"
        mine = make_repo(os.path.join(self.root, "games", "Mine"))
        other = make_repo(os.path.join(self.root, "games", "Other"))
        with contextlib.redirect_stdout(io.StringIO()):
            wp.main(["new", "--goal", "g", "--where", other], root=self.root)
            wp.main(["new", "--goal", "g", "--where", mine], root=self.root)  # the pointer now names Mine
        pointer = os.path.join(self.root, ".claude", "plan-sessions", "sess-show")
        with contextlib.redirect_stdout(io.StringIO()):
            wp.main(["show", "--where", other], root=self.root)
        with open(pointer, encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), os.path.join(mine, "docs", wp.NAME))
        os.remove(pointer)
        with contextlib.redirect_stdout(io.StringIO()):
            wp.main(["show", "--where", other], root=self.root)
        self.assertFalse(os.path.exists(pointer))

    def test_no_session_id_or_an_unsafe_one_writes_no_pointer(self):
        project = make_repo(os.path.join(self.root, "games", "Fresh"))
        folder = os.path.join(self.root, ".claude", "plan-sessions")
        with contextlib.redirect_stdout(io.StringIO()):
            wp.main(["new", "--goal", "g", "--where", project], root=self.root)
        self.assertFalse(os.path.exists(folder))
        os.environ["CLAUDE_CODE_SESSION_ID"] = "../../escape"
        with contextlib.redirect_stdout(io.StringIO()):
            wp.main(["show", "--where", project], root=self.root)
        self.assertFalse(os.path.exists(folder))
        self.assertFalse(os.path.exists(os.path.join(self.root, "escape")))

    def test_the_documented_variable_is_read_first_and_the_short_name_is_a_fallback(self):
        os.environ["CLAUDE_SESSION_ID"] = "short"
        self.assertEqual(wp.session_id_from_env(), "short")
        os.environ["CLAUDE_CODE_SESSION_ID"] = "documented"
        self.assertEqual(wp.session_id_from_env(), "documented")

    def test_the_hook_is_silent_when_there_is_no_plan_at_all(self):
        with tempfile.TemporaryDirectory() as empty:
            empty = os.path.realpath(empty)
            out = with_stdin(json.dumps({"session_id": "s", "cwd": empty}), ["hook"], root=empty)
            self.assertEqual(out, "")


class Bundle(unittest.TestCase):
    """done --bundle: the stage closes only on a command that ran and exited 0, and keeps the evidence."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)
        self.project = make_repo(os.path.join(self.root, "group", "app"))
        self.path = os.path.join(self.project, "docs", wp.NAME)
        isolate(self)
        self.main("new", "--goal", "g")
        self.main("add", "--title", "one", "--done-when", "x")
        self.main("add", "--title", "two", "--done-when", "y")
        self.main("start", "1")

    def main(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            wp.main(list(args) + ["--where", self.project], root=self.root)
        return out.getvalue()

    def done(self, *command, extra=(), n="1"):
        """`done N --result r [extra] --where P --bundle -- command`; returns the printed plan."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            wp.main(["done", n, "--result", "r", *extra, "--where", self.project, "--bundle", "--", *command],
                    root=self.root)
        return out.getvalue()

    def refused(self, *command, extra=()):
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stdout(io.StringIO()):
            self.done(*command, extra=extra)
        return str(caught.exception.code)

    def plan(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def test_a_passing_command_closes_the_stage_and_records_the_evidence(self):
        self.main("note", "1", "a note written while the stage ran")
        out = self.done(PY, "-c", "import sys; sys.stdout.buffer.write(b'3 passed\\n')")
        one = self.plan()["stages"][0]
        self.assertEqual(one["status"], "done")
        v = one["verified"]
        self.assertEqual((v["exit"], v["tail"]), (0, "3 passed"))
        self.assertEqual(v["sha256"], hashlib.sha256(b"3 passed\n").hexdigest())
        self.assertGreaterEqual(v["duration_s"], 0)
        self.assertIn("-c", v["command"])
        self.assertTrue(v["time"])
        self.assertRegex(one["proof"], r"^verified: .* exit 0 in \d+\.\d s$")
        self.assertIn(v["command"], one["proof"])
        self.assertEqual(one["notes"], [])
        self.assertIn("[done] 1. one [verified]: r (proof: verified: ", out)
        self.assertIn("next: stage 2, two", out)

    def test_the_command_runs_from_the_project_folder(self):
        self.done(PY, "-c", "import os; print(os.getcwd())")
        self.assertEqual(os.path.realpath(self.plan()["stages"][0]["verified"]["tail"]), os.path.realpath(self.project))

    def test_the_commands_own_options_reach_it_untouched(self):
        self.done(PY, "-c", "import sys; print(sys.argv[1:])", "--result", "--where", "-x")
        self.assertEqual(self.plan()["stages"][0]["verified"]["tail"], "['--result', '--where', '-x']")

    def test_a_failing_command_refuses_to_close_and_prints_the_tail(self):
        message = self.refused(PY, "-c", "import sys; print('boom line'); sys.exit(3)")
        self.assertIn("exit 3", message)
        self.assertIn("boom line", message)
        one = self.plan()["stages"][0]
        self.assertEqual(one["status"], "in_progress")
        self.assertNotIn("verified", one)
        self.assertNotIn("proof", one)

    def test_a_failure_keeps_the_notes_of_the_open_stage(self):
        self.main("note", "1", "waiting on it")
        self.refused(PY, "-c", "raise SystemExit(1)")
        self.assertEqual(self.plan()["stages"][0]["notes"], ["waiting on it"])

    def test_a_timeout_refuses_to_close(self):
        message = self.refused(PY, "-c", "import time; time.sleep(30)", extra=("--timeout", "0.5"))
        self.assertIn("timed out", message)
        self.assertEqual(self.plan()["stages"][0]["status"], "in_progress")

    def test_an_outward_command_is_refused_and_never_run(self):
        marker = os.path.join(self.root, "ran")
        for command in ([PY, "-c", f"open({marker!r}, 'w')", "--execute"],
                        ["git", "push", "origin", "main"],
                        ["bash", f"{self.root}/scripts/my-git.sh", "push"]):
            self.assertIn("refused", self.refused(*command))
        self.assertFalse(os.path.exists(marker))
        self.assertEqual(self.plan()["stages"][0]["status"], "in_progress")

    def test_a_recursive_rm_is_refused(self):
        victim = os.path.join(self.root, "victim")
        os.makedirs(victim)
        self.assertIn("refused", self.refused("rm", "-rf", victim))
        self.assertTrue(os.path.isdir(victim))

    def test_proof_still_closes_a_stage_and_carries_no_marker(self):
        out = self.main("done", "1", "--result", "built", "--proof", "ShopTests 6 passed")
        one = self.plan()["stages"][0]
        self.assertEqual((one["status"], one["proof"]), ("done", "ShopTests 6 passed"))
        self.assertNotIn("verified", one)
        self.assertNotIn("[verified]", out)
        self.assertIn("[done] 1. one [unverified]: built (proof: ShopTests 6 passed)", out)

    def test_bundle_and_proof_together_or_without_a_command_are_refused(self):
        self.assertIn("no --proof", self.refused(PY, "-c", "pass", extra=("--proof", "text")))
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stdout(io.StringIO()):
            wp.main(["done", "1", "--result", "r", "--bundle", "--where", self.project], root=self.root)
        self.assertIn("command after", str(caught.exception.code))
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stdout(io.StringIO()):
            wp.main(["note", "1", "t", "--bundle", "--where", self.project], root=self.root)
        self.assertIn("belongs to done", str(caught.exception.code))
        self.assertEqual(self.plan()["stages"][0]["status"], "in_progress")

    def test_a_stage_not_in_progress_is_refused_before_anything_runs(self):
        marker = os.path.join(self.root, "ran")
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stdout(io.StringIO()):
            self.done(PY, "-c", f"open({marker!r}, 'w')", n="2")
        self.assertIn("start it first", str(caught.exception.code))
        self.assertFalse(os.path.exists(marker))

    def test_the_stored_tail_is_the_end_of_the_output_within_2000_characters(self):
        self.done(PY, "-c", "for i in range(100): print('line %03d ' % i + 'x' * 60)")
        v = self.plan()["stages"][0]["verified"]
        self.assertLessEqual(len(v["tail"]), 2000)
        self.assertTrue(v["tail"].endswith("line 099 " + "x" * 60))
        self.assertTrue(v["tail"].startswith("..."))

    def test_a_double_dash_without_bundle_still_ends_the_options_for_a_note(self):
        with contextlib.redirect_stdout(io.StringIO()):
            wp.main(["note", "1", "--where", self.project, "--", "a note after the double dash"], root=self.root)
        self.assertEqual(self.plan()["stages"][0]["notes"], ["a note after the double dash"])

    def test_the_plan_stays_valid_json_and_one_stage_runs_at_a_time(self):
        self.done(PY, "-c", "pass")
        self.assertEqual([s["status"] for s in self.plan()["stages"]], ["done", "pending"])
        self.main("start", "2")
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stdout(io.StringIO()):
            self.main("start", "1")
        self.assertIn("is done", str(caught.exception.code))

    def test_show_and_the_hook_mark_a_verified_stage_and_only_it(self):
        self.done(PY, "-c", "pass")
        self.main("start", "2")
        self.main("done", "2", "--result", "r2", "--proof", "by eye")
        shown = self.main("show")
        self.assertIn("[done] 1. one [verified]: r (proof: verified: ", shown)
        self.assertIn("[done] 2. two [unverified]: r2 (proof: by eye)", shown)
        text = wp.hook_text(self.root, "startup", "", self.project)
        self.assertIn("  [done] 1. one [verified]\n", text)
        self.assertIn("  [done] 2. two [unverified]\n", text)
        self.assertEqual(text.count("[verified]"), 1)

    def test_the_root_plan_runs_its_command_from_the_root(self):
        with contextlib.redirect_stdout(io.StringIO()):
            for args in (["new", "--goal", "g"], ["add", "--title", "a", "--done-when", "x"], ["start", "1"]):
                wp.main(args + ["--where", self.root], root=self.root)
            wp.main(["done", "1", "--result", "r", "--where", self.root, "--bundle", "--",
                     PY, "-c", "import os; print(os.getcwd())"], root=self.root)
        with open(os.path.join(self.root, ".claude", wp.NAME), encoding="utf-8") as f:
            v = json.load(f)["stages"][0]["verified"]
        self.assertEqual(os.path.realpath(v["tail"]), os.path.realpath(self.root))


def where_first(args, project):
    """`args --where project`, with --where before a bundle's `--` so the command's own words stay untouched."""
    args = list(args)
    i = args.index("--") if "--" in args else len(args)
    return args[:i] + ["--where", project] + args[i:]


def reap(proc, timeout):
    """The exit code of a Popen made with pipes, after reading them (so none is left open)."""
    proc.communicate(timeout=timeout)
    return proc.returncode


SCRIPT = os.path.join(HERE, "work_plan.py")
WAIT_FOR = ("import os, sys, time\n"
            "open(sys.argv[1], 'w').close()\n"
            "while not os.path.exists(sys.argv[2]):\n"
            "    time.sleep(0.02)\n")


class Concurrency(unittest.TestCase):
    """Parallel commands never crash or lose a change; a bundle does not hold the plan while its command runs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)
        self.project = make_repo(os.path.join(self.root, "app"))
        self.path = os.path.join(self.project, "docs", wp.NAME)
        self.env = {k: v for k, v in os.environ.items() if k not in CLEAN_ENV}
        self.env["PYTHONUTF8"] = "1"
        for args in (["new", "--goal", "g"], ["add", "--title", "one", "--done-when", "x"],
                     ["add", "--title", "two", "--done-when", "y"], ["start", "1"]):
            self.assertEqual(self.cli(*args).returncode, 0)

    def popen(self, *args):
        return subprocess.Popen([PY, "-B", SCRIPT, *where_first(args, self.project)], env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def cli(self, *args):
        p = self.popen(*args)
        out, err = p.communicate(timeout=120)
        return subprocess.CompletedProcess(p.args, p.returncode, out, err)

    def plan(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def wait_for(self, marker):
        deadline = time.monotonic() + 60
        while not os.path.exists(marker):
            self.assertLess(time.monotonic(), deadline, "the bundle command never started")
            time.sleep(0.02)

    def test_thirty_parallel_notes_all_exit_zero_and_all_are_kept(self):
        procs = [self.popen("note", "1", f"parallel note {i}") for i in range(30)]
        results = [(p.communicate(timeout=180), p.returncode) for p in procs]
        self.assertEqual([(code, err) for (_, err), code in results], [(0, "")] * 30)
        notes = self.plan()["stages"][0]["notes"]
        self.assertEqual(sorted(notes), sorted(f"parallel note {i}" for i in range(30)))
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.path))), [wp.NAME],
                         "no temp or lock file is left beside the plan")

    def test_parallel_adds_get_distinct_ids(self):
        procs = [self.popen("add", "--title", f"t{i}", "--done-when", "x") for i in range(12)]
        self.assertEqual([reap(p, 180) for p in procs], [0] * 12)
        ids = [s["id"] for s in self.plan()["stages"]]
        self.assertEqual(ids, list(range(1, 15)))

    def test_a_note_and_a_stage_added_while_a_bundle_runs_survive_it(self):
        started, go = os.path.join(self.root, "started"), os.path.join(self.root, "go")
        bundle = self.popen("done", "1", "--result", "r", "--bundle", "--", PY, "-c", WAIT_FOR, started, go)
        self.wait_for(started)
        self.assertEqual(self.cli("note", "2", "written while the bundle runs").returncode, 0)
        self.assertEqual(self.cli("add", "--title", "three", "--done-when", "z").returncode, 0)
        open(go, "w").close()
        out, err = bundle.communicate(timeout=120)
        self.assertEqual(bundle.returncode, 0, err)
        stages = self.plan()["stages"]
        self.assertEqual((stages[0]["status"], bool(stages[0].get("verified"))), ("done", True))
        self.assertEqual(stages[1]["notes"], ["written while the bundle runs"])
        self.assertEqual([s["title"] for s in stages], ["one", "two", "three"])

    def test_a_stage_changed_while_its_bundle_runs_is_not_closed_by_it(self):
        started, go = os.path.join(self.root, "started"), os.path.join(self.root, "go")
        bundle = self.popen("done", "1", "--result", "r", "--bundle", "--", PY, "-c", WAIT_FOR, started, go)
        self.wait_for(started)
        self.assertEqual(self.cli("pause", "1", "--reason", "waits").returncode, 0)
        open(go, "w").close()
        out, err = bundle.communicate(timeout=120)
        self.assertNotEqual(bundle.returncode, 0)
        self.assertIn("stage 1 is pending now", err)
        one = self.plan()["stages"][0]
        self.assertEqual(one["status"], "pending")
        self.assertNotIn("verified", one)
        self.assertEqual(one["notes"], ["Paused: waits"])

    def test_a_lock_held_too_long_ends_the_command_with_a_message_and_changes_nothing(self):
        lock = self.path + ".lock"
        open(lock, "w").close()
        self.addCleanup(lambda: os.path.exists(lock) and os.remove(lock))
        with mock.patch.object(wp, "LOCK_WAIT", 0.3), self.assertRaises(SystemExit) as caught:
            with wp.plan_lock(self.path):
                self.fail("the lock was not held by the other process")
        self.assertIn("stayed locked", str(caught.exception.code))
        self.assertEqual(self.plan()["stages"][0]["notes"], [])

    def test_a_stale_lock_of_a_dead_process_is_broken(self):
        lock = self.path + ".lock"
        open(lock, "w").close()
        old = time.time() - wp.LOCK_STALE - 60
        os.utime(lock, (old, old))
        self.assertEqual(self.cli("note", "1", "after a crash").returncode, 0)
        self.assertEqual(self.plan()["stages"][0]["notes"], ["after a crash"])
        self.assertFalse(os.path.exists(lock))

    def test_the_lock_is_released_when_the_command_fails(self):
        self.assertNotEqual(self.cli("start", "9").returncode, 0)
        self.assertFalse(os.path.exists(self.path + ".lock"))

    def test_a_missing_plan_creates_no_folder_and_no_lock(self):
        bare = make_repo(os.path.join(self.root, "bare"))
        p = subprocess.run([PY, "-B", SCRIPT, "note", "1", "x", "--where", bare], env=self.env, capture_output=True,
                           text=True, timeout=60)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("no active plan", p.stderr)
        self.assertFalse(os.path.exists(os.path.join(bare, "docs")))

    def test_the_plan_is_written_through_its_own_temp_file_and_synced(self):
        calls = []
        real = os.fsync
        with mock.patch.object(os, "fsync", side_effect=lambda fd: (calls.append(fd), real(fd))[1]):
            wp.save(self.path, {"goal": "g", "created": "2026-01-01", "stages": []})
        self.assertEqual(len(calls), 1)
        self.assertEqual(os.listdir(os.path.dirname(self.path)), [wp.NAME])

    def test_replace_is_retried_on_a_windows_permission_error_and_then_gives_up(self):
        real = os.replace
        attempts = []

        def flaky(src, dst):
            attempts.append(1)
            if len(attempts) < 3:
                raise PermissionError("busy")
            real(src, dst)
        target = os.path.join(self.root, "target.txt")
        with mock.patch.object(wp, "WINDOWS", True), mock.patch.object(os, "replace", side_effect=flaky):
            wp.write_atomic(target, "text\n")
        self.assertEqual(len(attempts), 3)
        with open(target, encoding="utf-8") as f:
            self.assertEqual(f.read(), "text\n")
        with mock.patch.object(wp, "WINDOWS", True), mock.patch.object(wp, "REPLACE_WAIT", 0.1), \
                mock.patch.object(os, "replace", side_effect=PermissionError("busy")):
            with self.assertRaises(PermissionError):
                wp.write_atomic(target, "other\n")
        self.assertEqual(sorted(os.listdir(self.root)), ["app", "target.txt"], "the temp file is removed on failure")

    def test_parallel_commands_with_a_plans_root_leave_a_whole_pointer(self):
        plans = os.path.join(self.root, "plans")
        os.makedirs(plans)
        project = make_repo(os.path.join(plans, "shop"))
        env = dict(self.env, PROOF_PLANS_ROOT=plans, CLAUDE_CODE_SESSION_ID="sess-parallel")

        def run(*args):
            return subprocess.Popen([PY, "-B", SCRIPT, *args, "--where", project], env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True)
        first = run("new", "--goal", "g")
        self.assertEqual(reap(first, 60), 0)
        add = run("add", "--title", "a", "--done-when", "x")
        self.assertEqual(reap(add, 60), 0)
        self.assertEqual(reap(run("start", "1"), 60), 0)
        procs = [run("note", "1", f"n{i}") for i in range(15)]
        self.assertEqual([reap(p, 180) for p in procs], [0] * 15)
        with open(os.path.join(plans, ".claude", "plan-sessions", "sess-parallel"), encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), os.path.join(project, "docs", wp.NAME))
        self.assertEqual(os.listdir(os.path.join(plans, ".claude", "plan-sessions")), ["sess-parallel"])


class Unverified(unittest.TestCase):
    """A stage closed with --proof ran no command: show and the hook say so; a --bundle stage says [verified]."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = make_repo(os.path.join(os.path.realpath(self.tmp.name), "app"))
        isolate(self)

    def main(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            wp.main(where_first(args, self.project), root=None)
        return out.getvalue()

    def test_any_proof_text_marks_the_stage_unverified_in_every_view_and_a_bundle_marks_it_verified(self):
        self.main("new", "--goal", "g")
        for title in ("typed", "ran", "open"):
            self.main("add", "--title", title, "--done-when", "x")
        self.main("start", "1")
        change = self.main("done", "1", "--result", "r1", "--proof", "anything at all")
        self.assertIn("[done] 1. typed [unverified]: r1 (proof: anything at all)", change)
        self.main("start", "2")
        self.main("done", "2", "--result", "r2", "--bundle", "--", PY, "-c", "pass")
        shown = self.main("show")
        self.assertIn("[done] 1. typed [unverified]: r1", shown)
        self.assertIn("[done] 2. ran [verified]: r2", shown)
        hook = wp.hook_text(None, "startup", "", self.project)
        self.assertIn("  [done] 1. typed [unverified]\n", hook)
        self.assertIn("  [done] 2. ran [verified]\n", hook)

    def test_the_schema_is_unchanged_a_proof_close_stores_no_verified_key(self):
        self.main("new", "--goal", "g")
        self.main("add", "--title", "a", "--done-when", "x")
        self.main("start", "1")
        self.main("done", "1", "--result", "r", "--proof", "p")
        with open(os.path.join(self.project, "docs", wp.NAME), encoding="utf-8") as f:
            self.assertNotIn("verified", json.load(f)["stages"][0])


def find_bash():
    """A POSIX shell; on Windows Git Bash, never System32's WSL launcher."""
    if os.name != "nt":
        return shutil.which("sh") or shutil.which("bash")
    for path in (os.environ.get("CLAUDE_CODE_GIT_BASH_PATH"), shutil.which("bash"), r"C:\Program Files\Git\bin\bash.exe"):
        if path and os.path.isfile(path) and "system32" not in path.lower():
            return path
    return None


BASH = find_bash()


def posix(path):
    return path.replace("\\", "/")


@unittest.skipIf(BASH is None, "no POSIX shell found")
class Launchers(unittest.TestCase):
    """The hook through run-hook.cmd and the bin/ wrappers, started the way Claude Code starts them."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = os.path.realpath(self.tmp.name)
        self.project = make_repo(os.path.join(self.base, "my project"))  # a space in the path on purpose
        write_json(os.path.join(self.project, "docs", wp.NAME), "Launcher goal",
                   [stage(1, "in_progress", "launcher stage", notes=["launcher note"])])
        self.env = {k: v for k, v in os.environ.items() if k not in CLEAN_ENV}
        self.env["PYTHONUTF8"] = "1"

    def sh(self, script, stdin="", env=None, cwd=None):
        return subprocess.run([BASH, "-c", script], input=stdin, capture_output=True, text=True, timeout=120,
                              env=env or self.env, cwd=cwd or self.base)

    def test_the_manifest_hook_command_runs_through_the_launcher_with_session_json_on_stdin(self):
        with open(os.path.join(PLUGIN, "hooks", "hooks.json"), encoding="utf-8") as f:
            entry = json.load(f)["hooks"]["SessionStart"][0]
        self.assertEqual(entry["matcher"], "startup|resume|clear|compact")
        command = entry["hooks"][0]["command"]
        self.assertEqual(command, '"${CLAUDE_PLUGIN_ROOT}/hooks/run-hook.cmd" work_plan.py hook')
        env = dict(self.env, CLAUDE_PLUGIN_ROOT=posix(PLUGIN))
        payload = json.dumps({"session_id": "s", "source": "startup", "cwd": self.project})
        p = self.sh(command, stdin=payload, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("[now] 1. launcher stage", p.stdout)
        self.assertIn("launcher note", p.stdout)
        self.assertIn("Active plan of this project", p.stdout)

    def test_the_hook_reads_the_plans_root_option_from_its_environment(self):
        other = make_repo(os.path.join(self.base, "other"))
        write_json(os.path.join(other, "docs", wp.NAME), "Other goal", [stage(1, "pending", "other stage")])
        env = dict(self.env, CLAUDE_PLUGIN_ROOT=posix(PLUGIN), CLAUDE_PLUGIN_OPTION_PLANS_ROOT=self.base)
        command = '"${CLAUDE_PLUGIN_ROOT}/hooks/run-hook.cmd" work_plan.py hook'
        p = self.sh(command, stdin=json.dumps({"source": "startup", "cwd": self.base}), env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("  other/docs/ACTIVE-PLAN.json | Other goal | 0/1 done | next: 1. other stage | 0d", p.stdout)
        self.assertIn("  my project/docs/ACTIVE-PLAN.json | Launcher goal | 0/1 done | now: 1. launcher stage | 0d", p.stdout)
        p = self.sh(command, stdin=json.dumps({"source": "startup", "cwd": self.base}),
                    env=dict(self.env, CLAUDE_PLUGIN_ROOT=posix(PLUGIN)))
        self.assertEqual(p.stdout, "", "without a plans root nothing is indexed")

    @unittest.skipUnless(os.name == "nt", "the cmd.exe path is Windows only")
    def test_the_launcher_also_runs_under_cmd_exe(self):
        launcher = os.path.join(PLUGIN, "hooks", "run-hook.cmd")
        p = subprocess.run(["cmd.exe", "/c", launcher, "work_plan.py", "hook"], capture_output=True, text=True,
                           timeout=120, env=self.env, input=json.dumps({"source": "startup", "cwd": self.project}))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("[now] 1. launcher stage", p.stdout)

    def test_the_work_plan_wrapper_keeps_a_quoted_multi_word_argument_whole(self):
        wrapper = posix(os.path.join(PLUGIN, "bin", "work-plan"))
        p = subprocess.run([BASH, wrapper, "note", "1", "two words and 'a quote' inside", "--where", self.project],
                           capture_output=True, text=True, timeout=120, env=self.env, cwd=self.base)
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(os.path.join(self.project, "docs", wp.NAME), encoding="utf-8") as f:
            notes = json.load(f)["stages"][0]["notes"]
        self.assertEqual(notes[-1], "two words and 'a quote' inside")
        self.assertIn("ACTIVE PLAN", p.stdout)

    def test_the_wrapper_is_found_by_name_on_the_path_and_runs_from_a_subfolder(self):
        sub = os.path.join(self.project, "src")
        os.makedirs(sub)
        env = dict(self.env, PATH=posix(os.path.join(PLUGIN, "bin")) + ":" + self.env.get("PATH", ""))
        p = self.sh('work-plan add --title "a title with spaces" --done-when "tests pass; output read"', env=env, cwd=sub)
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        with open(os.path.join(self.project, "docs", wp.NAME), encoding="utf-8") as f:
            added = json.load(f)["stages"][-1]
        self.assertEqual((added["title"], added["done_when"]), ("a title with spaces", "tests pass; output read"))

    def test_the_wrapper_exit_code_and_message_pass_through(self):
        wrapper = posix(os.path.join(PLUGIN, "bin", "work-plan"))
        p = subprocess.run([BASH, wrapper, "start", "9", "--where", self.project], capture_output=True, text=True,
                           timeout=120, env=self.env)
        self.assertEqual(p.returncode, 1)
        self.assertIn("no stage 9", p.stderr)

    def test_the_verify_run_wrapper_passes_the_exit_code_and_a_quoted_argument(self):
        wrapper = posix(os.path.join(PLUGIN, "bin", "verify-run"))
        code = "import sys; print(sys.argv[1]); sys.exit(4)"
        p = subprocess.run([BASH, wrapper, "run", "--cwd", self.project, "--", PY, "-c", code, "two words"],
                           capture_output=True, text=True, timeout=120, env=self.env)
        self.assertEqual(p.returncode, 1, p.stderr)
        result = json.loads(p.stdout)
        self.assertEqual((result["exit"], result["tail"]), (4, ["two words"]))
        ok = subprocess.run([BASH, wrapper, "run", "--cwd", self.project, "--", PY, "-c", "pass"],
                            capture_output=True, text=True, timeout=120, env=self.env)
        self.assertEqual(ok.returncode, 0, ok.stderr)

    def test_the_wrappers_are_posix_sh_with_lf_endings(self):
        for name in ("work-plan", "verify-run"):
            with open(os.path.join(PLUGIN, "bin", name), "rb") as f:
                raw = f.read()
            self.assertTrue(raw.startswith(b"#!/bin/sh\n"), name)
            self.assertNotIn(b"\r", raw)
            for word in (b"python3", b"python", b"py -3", b'-c ""'):
                self.assertIn(word, raw, name)


if __name__ == "__main__":
    unittest.main()
