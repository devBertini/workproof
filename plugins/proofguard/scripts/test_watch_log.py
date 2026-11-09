#!/usr/bin/env python
"""Tests for watch_log.py: a log follower for the Monitor tool that exits with the run it watches.

The script runs as a subprocess with --interval 0.05 under a time bound; lines are appended by the test.

  python -B scripts/test_watch_log.py
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watch_log  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("WATCH_SCRIPT") or os.path.join(HERE, "watch_log.py")


class WatchLog(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.log = os.path.join(self._tmp.name, "run.log")
        self.procs = []
        self.addCleanup(self._kill)

    def _kill(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
                p.wait()

    def start(self, *extra):
        p = subprocess.Popen([sys.executable, "-B", SCRIPT, self.log, "--interval", "0.05"] + list(extra),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.procs.append(p)
        return p

    def write(self, text):
        with open(self.log, "a") as handle:
            handle.write(text)

    def finish(self, p, bound=10):
        out, _ = p.communicate(timeout=bound)
        return p.returncode, out.splitlines()

    def test_exits_at_end_line_while_log_stays_quiet(self):
        self.write("compile a\nerror: bad\n")
        p = self.start("--events", "error:")
        time.sleep(0.3)
        self.write("more\n** BUILD SUCCEEDED **\nDONE rc=0\n")
        code, lines = self.finish(p)
        self.assertEqual(code, 0)
        self.assertEqual(lines, ["error: bad", "DONE rc=0"])

    def test_custom_end_and_width(self):
        self.write("x" * 50 + " FAIL\n** TEST FAILED **\nafter\n")
        code, lines = self.finish(self.start("--events", "FAIL", "--end", r"TEST (SUCCEEDED|FAILED)", "--width", "10"))
        self.assertEqual((code, lines), (0, ["x" * 10, "** TEST FA"]))

    def test_a_git_bash_path_becomes_a_windows_path(self):
        self.assertEqual(watch_log.native_path("/c/work/run.log"), "C:/work/run.log")
        self.assertEqual(watch_log.native_path("C:/work/run.log"), "C:/work/run.log")

    def test_writer_gone_without_end_line(self):
        writer = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.6)"])
        self.procs.append(writer)
        self.write("error: one\nlast line no newline error: two")
        code, lines = self.finish(self.start("--events", "error:", "--pid", str(writer.pid)))
        self.assertEqual(code, 4)
        self.assertEqual(lines, ["error: one", "last line no newline error: two",
                                 "WRITER EXITED pid %d, no end line" % writer.pid])

    def test_idle_is_on_by_default_and_zero_turns_it_off(self):
        self.write("start\n")
        p = self.start("--idle", "0")
        time.sleep(0.5)
        self.assertIsNone(p.poll())
        p.kill()
        p.wait()
        with open(SCRIPT) as handle:
            self.assertIn('"--idle", type=float, default=1200', handle.read())

    def test_idle(self):
        self.write("start\n")
        code, lines = self.finish(self.start("--idle", "0.4"))
        self.assertEqual(code, 5)
        self.assertTrue(lines[-1].startswith("IDLE 0.4 s"))

    def test_waits_for_log_then_gives_up(self):
        p = self.start("--events", "e")
        time.sleep(0.3)
        self.write("e1\nDONE rc=1\n")
        self.assertEqual(self.finish(p), (0, ["e1", "DONE rc=1"]))
        os.remove(self.log)
        code, lines = self.finish(self.start("--wait-file", "0.3"))
        self.assertEqual(code, 3)
        self.assertTrue(lines[0].startswith("NO LOG "))

    def test_from_end_skips_old_success_line(self):
        self.write("DONE rc=0\n")
        p = self.start("--from-end")
        time.sleep(0.3)
        self.assertIsNone(p.poll())
        self.write("DONE rc=2\n")
        self.assertEqual(self.finish(p), (0, ["DONE rc=2"]))

    def test_truncated_log_is_read_again(self):
        self.write("old line one\nold line two\n")
        p = self.start("--events", "new")
        time.sleep(0.3)
        with open(self.log, "w") as handle:
            handle.write("new\n")
        time.sleep(0.3)
        self.write("DONE rc=0\n")
        self.assertEqual(self.finish(p), (0, ["new", "DONE rc=0"]))

    @unittest.skipIf(os.name == "nt", "Windows has no SIGTERM: terminate() ends the process at once")
    def test_sigterm_is_quiet(self):
        self.write("x\n")
        p = self.start()
        time.sleep(0.3)
        p.terminate()
        out, err = p.communicate(timeout=5)
        self.assertEqual((p.returncode, err), (0, ""))


if __name__ == "__main__":
    unittest.main()
