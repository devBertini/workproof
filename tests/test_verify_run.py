#!/usr/bin/env python
"""Tests for verify_run.py. Run: python -B test_verify_run.py

Expected values come from independent sources here (hashlib, Python's own output), never from verify_run.py."""
import hashlib
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
TOOL = os.path.join(HERE, "verify_run.py")
PY = sys.executable
sys.path.insert(0, HERE)
import verify_run  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = os.path.realpath(self._tmp.name)

    def tool(self, *args, stdin=""):
        return subprocess.run([PY, "-B", TOOL, *args], input=stdin, capture_output=True, text=True, timeout=60)

    def run_cmd(self, *command, options=("--timeout", "20"), stdin=""):
        p = self.tool("run", "--cwd", self.dir, *options, "--", *command, stdin=stdin)
        try:
            return p, json.loads(p.stdout)
        except ValueError:
            self.fail(f"no JSON on stdout: {p.stdout!r} stderr: {p.stderr!r}")


class Run(Base):
    def test_a_passing_command_is_recorded_with_independent_evidence(self):
        code = "import sys; sys.stdout.buffer.write(b'out\\n'); sys.stdout.flush(); sys.stderr.buffer.write(b'err\\n')"
        p, r = self.run_cmd(PY, "-c", code)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual((r["exit"], r["timed_out"], r["shell"], r["cwd"]), (0, False, False, self.dir))
        self.assertEqual(r["tail"], ["out", "err"])
        self.assertEqual(r["output_bytes"], len(b"out\nerr\n"))
        self.assertEqual(r["sha256"], hashlib.sha256(b"out\nerr\n").hexdigest())
        self.assertIsInstance(r["duration_s"], float)
        self.assertRegex(r["time"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")
        self.assertEqual(sorted(r), sorted(["command", "shell", "cwd", "exit", "timed_out", "duration_s",
                                            "output_bytes", "sha256", "tail", "time"]))

    def test_a_failing_command_exits_1_with_its_own_exit_code(self):
        p, r = self.run_cmd(PY, "-c", "import sys; print('bad'); sys.exit(5)")
        self.assertEqual((p.returncode, r["exit"], r["timed_out"], r["tail"]), (1, 5, False, ["bad"]))

    def test_the_duration_is_measured(self):
        _, r = self.run_cmd(PY, "-c", "import time; time.sleep(0.6)")
        self.assertGreaterEqual(r["duration_s"], 0.55)
        self.assertLess(r["duration_s"], 10)

    def test_a_timeout_kills_the_whole_process_tree_and_fails(self):
        marker = os.path.join(self.dir, "grandchild")
        child = "import sys, time; time.sleep(2); open(sys.argv[1], 'w')"
        code = ("import subprocess, sys, time\n"
                f"subprocess.Popen([sys.executable, '-c', {child!r}, {marker!r}])\n"
                "time.sleep(60)")
        started = time.monotonic()
        p, r = self.run_cmd(PY, "-c", code, options=("--timeout", "0.7"))
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual((p.returncode, r["timed_out"], r["exit"]), (1, True, 124))
        time.sleep(3)
        self.assertFalse(os.path.exists(marker), "the grandchild survived the timeout")

    def test_the_tail_is_the_last_40_lines_and_the_hash_covers_everything(self):
        _, r = self.run_cmd(PY, "-c", "import sys\nfor i in range(100): sys.stdout.buffer.write(b'line %d\\n' % i)")
        self.assertEqual(r["tail"], [f"line {i}" for i in range(60, 100)])
        full = "".join(f"line {i}\n" for i in range(100)).encode()
        self.assertEqual((r["output_bytes"], r["sha256"]), (len(full), hashlib.sha256(full).hexdigest()))

    def test_an_output_without_a_final_newline_keeps_its_last_line(self):
        _, r = self.run_cmd(PY, "-c", "import sys; sys.stdout.write('a\\nb')")
        self.assertEqual(r["tail"], ["a", "b"])

    def test_no_output_has_the_hash_of_nothing(self):
        _, r = self.run_cmd(PY, "-c", "pass")
        self.assertEqual((r["tail"], r["output_bytes"], r["sha256"]), ([], 0, hashlib.sha256(b"").hexdigest()))

    def test_it_runs_from_cwd_and_stdin_is_closed(self):
        _, r = self.run_cmd(PY, "-c", "import os, sys; print(os.getcwd()); print(repr(sys.stdin.read()))", stdin="data")
        self.assertEqual(r["tail"], [self.dir, "''"])

    def test_a_shell_line_runs_only_with_the_flag(self):
        p, shell = self.run_cmd("echo a | tr a b", options=("--timeout", "20", "--shell"))
        self.assertEqual((p.returncode, shell["tail"], shell["shell"]), (0, ["b"], True))
        p, r = self.run_cmd("echo a | tr a b")
        self.assertEqual((p.returncode, r["exit"]), (1, 127))  # without the flag the whole line is one program name

    def test_a_pipeline_fails_when_any_stage_fails_in_every_way_to_start_one(self):
        cases = [(["false | tail -1"], ("--timeout", "20", "--shell")),
                 (["bash", "-c", "false | tail -1"], ("--timeout", "20")),
                 (["sh", "-c", "false | tail -1"], ("--timeout", "20")),
                 (["bash", "-lc", "false | tail -1"], ("--timeout", "20"))]
        for command, options in cases:
            p, r = self.run_cmd(*command, options=options)
            self.assertEqual((p.returncode, r["exit"]), (1, 1), command)

    def test_a_passing_pipeline_still_passes_and_its_arguments_survive(self):
        for command, options in ((["echo ok | tail -1"], ("--timeout", "20", "--shell")),
                                 (["bash", "-c", "echo ok | tail -1"], ("--timeout", "20")),
                                 (["sh", "-c", "echo ok | tail -1"], ("--timeout", "20"))):
            p, r = self.run_cmd(*command, options=options)
            self.assertEqual((p.returncode, r["tail"]), (0, ["ok"]), command)
        _, r = self.run_cmd("bash", "-c", 'echo "$1 $2"', "name", "first", "second")
        self.assertEqual(r["tail"], ["first second"])
        p, r = self.run_cmd("sh", "-c", "exit 3")
        self.assertEqual((p.returncode, r["exit"]), (1, 3))

    def test_the_pipeline_start_is_planned_with_pipefail_and_bash_for_sh(self):
        bash = verify_run.BASH
        self.assertEqual(verify_run.launch_plan(["a | b"], True), ([bash, "-c", "set -o pipefail; a | b"], False, None))
        self.assertEqual(verify_run.launch_plan(["bash", "-c", "x", "n"], False),
                         ([bash, "-c", "set -o pipefail; x", "n"], False, None))
        self.assertEqual(verify_run.launch_plan(["/bin/zsh", "-c", "x"], False)[0][2], "set -o pipefail; x")
        self.assertEqual(verify_run.launch_plan(["sh", "-c", "x"], False)[0][:2], [bash, "-c"])
        self.assertEqual(verify_run.launch_plan(["python", "-c", "x"], False), (["python", "-c", "x"], False, None))
        self.assertEqual(verify_run.launch_plan(["bash", "script.sh"], False), (["bash", "script.sh"], False, None))

    def test_a_bare_pipe_word_without_a_shell_is_refused_with_a_clear_message(self):
        p = self.tool("run", "--cwd", self.dir, "--", "echo", "a", "|", "tail", "-1")
        self.assertEqual(p.returncode, 2)
        self.assertIn("needs a shell", p.stderr)
        self.assertIn("refused", json.loads(p.stdout))
        self.assertEqual(self.tool("run", "--cwd", self.dir, "--shell", "--", "echo", "a", "|", "tail", "-1").returncode, 0)

    def test_a_command_that_does_not_exist_fails_with_127(self):
        p, r = self.run_cmd("definitely-not-a-command-xyz")
        self.assertEqual((p.returncode, r["exit"]), (1, 127))
        self.assertIn("cannot run", r["tail"][0])

    def test_misuse_exits_2_with_a_reason(self):
        for args, word in ((("run", "--cwd", self.dir), "no command"),
                           (("run", "--cwd", os.path.join(self.dir, "nope"), "--", "true"), "not a folder"),
                           (("run", "--cwd", self.dir, "--timeout", "0", "--", "true"), "positive"),
                           (("run", "--cwd", self.dir, "--timeout", "-3", "--", "true"), "positive")):
            p = self.tool(*args)
            self.assertEqual(p.returncode, 2, args)
            self.assertIn(word, p.stderr)

    def test_bash_is_never_the_system32_launcher(self):
        self.assertNotIn("system32", verify_run.BASH.lower())


class BatchShims(Base):
    """A .cmd or .bat shim (what `npm`, `npx`, `yarn` are on Windows) starts through cmd.exe, found by PATHEXT."""

    def make_shim(self, name="shim.cmd", folder="bin dir"):
        shims = os.path.join(self.dir, folder)  # a space in the folder on purpose
        os.makedirs(shims)
        with open(os.path.join(shims, name), "w", newline="") as f:
            f.write("@echo off\r\necho shim[%~1][%~2]\r\nexit /b 7\r\n")
        return shims

    def on_path(self, shims):
        return {**os.environ, "PATH": shims + os.pathsep + os.environ.get("PATH", "")}

    def test_batch_line_quotes_arguments_and_refuses_what_cmd_would_read_as_syntax(self):
        self.assertEqual(verify_run.batch_line("C:\\t\\npm.cmd", ["test", "two words"]),
                         'C:\\t\\npm.cmd test "two words"')
        for bad in ("a&b", "a|b", "a<b", "a>b", "a^b", "50%", 'say "hi"', "a\nb", "a\rb"):
            with self.assertRaises(verify_run.Refused, msg=bad) as caught:
                verify_run.batch_line("x.cmd", ["ok", bad])
            self.assertIn("cmd.exe cannot take", str(caught.exception))
        for fine in ("--grep=a b", "-x", "C:\\a b\\c", "(paren)", "a,b;c=d", "!bang"):
            verify_run.batch_line("x.cmd", [fine])

    @unittest.skipIf(os.name == "nt", "off Windows nothing is resolved or wrapped")
    def test_off_windows_the_command_is_started_as_given(self):
        self.assertEqual(verify_run.resolve_program(["npm", "test"]), ["npm", "test"])

    @unittest.skipUnless(os.name == "nt", "Windows runs .cmd shims through cmd.exe")
    def test_a_cmd_shim_found_on_path_runs_with_its_arguments_and_exit_code(self):
        shims = self.make_shim()
        with mock.patch.dict(os.environ, self.on_path(shims)):
            r = verify_run.run_command(["shim", "two words", "-x"], self.dir, 30)
        self.assertEqual((r["exit"], r["timed_out"], r["tail"]), (7, False, ["shim[two words][-x]"]))
        self.assertEqual(r["command"], "shim 'two words' -x")

    @unittest.skipUnless(os.name == "nt", "Windows runs .cmd shims through cmd.exe")
    def test_a_bat_shim_and_the_cli_exit_code_and_a_zero_exit(self):
        shims = self.make_shim("tool.bat")
        p = subprocess.run([PY, "-B", TOOL, "run", "--cwd", self.dir, "--", "tool", "a", "b"], env=self.on_path(shims),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertEqual(json.loads(p.stdout)["tail"], ["shim[a][b]"])
        with open(os.path.join(shims, "tool.bat"), "w", newline="") as f:
            f.write("@echo off\r\necho fine\r\n")
        p = subprocess.run([PY, "-B", TOOL, "run", "--cwd", self.dir, "--", "tool.bat"], env=self.on_path(shims),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual((p.returncode, json.loads(p.stdout)["tail"]), (0, ["fine"]))

    @unittest.skipUnless(os.name == "nt", "Windows runs .cmd shims through cmd.exe")
    def test_an_argument_cmd_cannot_carry_is_refused_and_the_shim_never_runs(self):
        shims = self.make_shim()
        marker = os.path.join(self.dir, "ran")
        p = subprocess.run([PY, "-B", TOOL, "run", "--cwd", self.dir, "--", "shim", f"x&echo>{marker}"],
                           env=self.on_path(shims), capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 2)
        self.assertIn("cmd.exe cannot take", p.stderr)
        self.assertIn("refused", json.loads(p.stdout))
        self.assertFalse(os.path.exists(marker))

    @unittest.skipUnless(os.name == "nt", "Windows runs .cmd shims through cmd.exe")
    def test_a_program_that_is_not_a_shim_still_runs_as_before_and_a_missing_one_says_so(self):
        _, r = self.run_cmd("python", "-c", "print('plain')") if shutil.which("python") else (None, {"tail": ["plain"]})
        self.assertEqual(r["tail"], ["plain"])
        _, r = self.run_cmd("definitely-not-a-command-xyz")
        self.assertEqual(r["exit"], 127)
        self.assertIn("cannot run definitely-not-a-command-xyz", r["tail"][0])


class Refusals(Base):
    OUTWARD = [
        ("--execute", [PY, "-c", "pass", "--execute"]),
        ("--execute in a shell line", ["sh", "-c", "echo hi --execute"]),
        ("git push", ["git", "push"]),
        ("git push with options before", ["git", "-C", "/x", "push", "origin", "main"]),
        ("git push in a shell line", ["sh", "-c", "git push"]),
        ("a wrapper named *-git", ["bash", "/a/scripts/my-git.sh", "push", "origin"]),
        ("rm -rf", ["rm", "-rf", "x"]),
        ("rm -fr", ["rm", "-fr", "x"]),
        ("rm -R", ["rm", "-R", "x"]),
        ("rm -f -r", ["rm", "-f", "-r", "x"]),
        ("rm --recursive", ["rm", "--force", "--recursive", "x"]),
        ("rm file -r", ["rm", "x", "-r"]),
        ("rm -rf in a shell line", ["sh", "-c", "cd /tmp && rm -rf x"]),
    ]

    def test_every_outward_or_destructive_command_is_refused_and_never_started(self):
        marker = os.path.join(self.dir, "ran")
        for name, command in self.OUTWARD:
            command = command + [f"{marker}-{abs(hash(name))}"]  # an argument no real command needs
            for options in (("--timeout", "20"), ("--timeout", "20", "--shell")):
                p = self.tool("run", "--cwd", self.dir, *options, "--", *command)
                self.assertEqual(p.returncode, 2, (name, options, p.stdout, p.stderr))
                self.assertIn("refused", p.stderr, name)
                self.assertIn("refused", json.loads(p.stdout), name)
        self.assertEqual(os.listdir(self.dir), [])

    def test_a_refused_command_does_not_run_even_when_it_would_leave_a_trace(self):
        marker = os.path.join(self.dir, "ran")
        p = self.tool("run", "--cwd", self.dir, "--", PY, "-c", f"open({marker!r}, 'w')", "--execute")
        self.assertEqual(p.returncode, 2)
        self.assertFalse(os.path.exists(marker))

    def test_harmless_commands_with_look_alike_words_are_not_refused(self):
        victim = os.path.join(self.dir, "file.txt")
        open(victim, "w").close()
        for command in (["rm", victim], ["rm", "-f", os.path.join(self.dir, "none")], ["echo", "push"],
                        ["echo", "firm", "-r"], ["echo", "git", "--rm", "-r"], ["git", "--version"],
                        ["echo", "test_push", "gitlab"], ["echo", "executor"]):
            p, r = self.run_cmd(*command)
            self.assertEqual(p.returncode, 0, (command, p.stderr))
        self.assertFalse(os.path.exists(victim))

    def test_the_refusal_is_the_same_through_the_module_api(self):
        self.assertIsNone(verify_run.refusal(["python", "-m", "unittest"]))
        self.assertIn("--execute", verify_run.refusal(["deploy.py", "--execute"]))
        with self.assertRaises(verify_run.Refused):
            verify_run.run_command(["git", "push"], self.dir)


class Check(Base):
    def good(self):
        _, r = self.run_cmd(PY, "-c", "print('ok')")
        return r

    def check(self, obj):
        return self.tool("check", json.dumps(obj) if not isinstance(obj, str) else obj)

    def test_the_output_of_run_passes_check_as_text_file_and_stdin(self):
        obj = self.good()
        p = self.check(obj)
        self.assertEqual((p.returncode, p.stderr), (0, ""))
        self.assertIn("valid:", p.stdout)
        path = os.path.join(self.dir, "evidence.json")
        with open(path, "w") as f:
            json.dump(obj, f)
        self.assertEqual(self.tool("check", path).returncode, 0)
        self.assertEqual(self.tool("check", "-", stdin=json.dumps(obj)).returncode, 0)

    def test_a_valid_object_of_a_failed_command_exits_1(self):
        _, r = self.run_cmd(PY, "-c", "import sys; sys.exit(2)")
        p = self.check(r)
        self.assertEqual(p.returncode, 1)
        self.assertIn("valid but failed", p.stdout)
        timed = dict(self.good(), timed_out=True, exit=124)
        self.assertEqual(self.check(timed).returncode, 1)

    def test_each_malformed_object_exits_2_and_names_the_field(self):
        good = self.good()
        empty = hashlib.sha256(b"").hexdigest()
        cases = {
            "command": dict(good, command=""),
            "shell": dict(good, shell="no"),
            "exit": dict(good, exit=True),
            "exit string": dict(good, exit="0"),
            "timed_out": dict(good, timed_out=0),
            "duration_s": dict(good, duration_s=-1),
            "output_bytes": dict(good, output_bytes=-1),
            "sha256": dict(good, sha256="abc"),
            "sha256 upper": dict(good, sha256=good["sha256"].upper()),
            "tail": dict(good, tail=["x"] * 41),
            "tail type": dict(good, tail=[1]),
            "time": dict(good, time=""),
            "cwd": {k: v for k, v in good.items() if k != "cwd"},
            "empty hash with bytes": dict(good, sha256=empty),
            "bytes 0 with a real hash": dict(good, output_bytes=0, tail=[]),
            "timed out with exit 0": dict(good, timed_out=True),
            "tail without bytes": dict(good, output_bytes=0, sha256=empty),
            "refusal": {"refused": "x"},
        }
        for name, obj in cases.items():
            p = self.check(obj)
            self.assertEqual(p.returncode, 2, (name, p.stdout))
            self.assertIn("invalid", p.stderr, name)
        self.assertIn("exit:", self.check(cases["exit"]).stderr)
        self.assertIn("sha256:", self.check(cases["sha256"]).stderr)

    def test_a_timed_out_run_never_counts_as_passed(self):
        self.assertTrue(verify_run.passed(self.good()))
        self.assertFalse(verify_run.passed(dict(self.good(), timed_out=True)))
        self.assertFalse(verify_run.passed(dict(self.good(), exit=1)))

    def test_unreadable_input_exits_2(self):
        for source in ("not json", "[1, 2]", os.path.join(self.dir, "missing.json"), "{broken"):
            self.assertEqual(self.check(source).returncode, 2, source)


if __name__ == "__main__":
    unittest.main()
