"""Tests for doctor.py (workproof-doctor) and the launcher self-test. Run: python -B test_doctor.py"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
HERE = os.path.join(os.path.dirname(TESTS), "plugins", "workproof", "scripts")  # the engines under test
sys.path.insert(0, HERE)
import doctor  # noqa: E402
import modules  # noqa: E402
from test_support import find_sh, posix  # noqa: E402

PLUGIN = os.path.dirname(HERE)


def scratch_plugin(folder):
    """A copy of what the doctor looks at: the manifest, the launcher, the scripts and the bin tools."""
    root = os.path.join(folder, "my plugin")
    for part in (".claude-plugin", "hooks", "scripts", "bin"):
        shutil.copytree(os.path.join(PLUGIN, part), os.path.join(root, part),
                        ignore=shutil.ignore_patterns("*.ts", "__pycache__", "types"))
    for base, _, names in os.walk(root):
        for name in names:
            if base.endswith(("hooks", "bin")):
                os.chmod(os.path.join(base, name), 0o755)
    return root


def report(root, environ=None):
    out = io.StringIO()
    code = doctor.run(root, environ if environ is not None else dict(os.environ), out)
    return code, out.getvalue()


class SelfTest(unittest.TestCase):
    def test_a_new_enough_python_passes_and_an_old_one_names_the_minimum(self):
        out = io.StringIO()
        self.assertEqual(modules.self_test((3, 10, 0), out), 0)
        self.assertIn("self-test ok: Python 3.10", out.getvalue())
        out = io.StringIO()
        self.assertEqual(modules.self_test((3, 9, 18), out), 1)
        self.assertIn("Python 3.9 is too old, the hooks need 3.10 or later", out.getvalue())

    def test_through_the_launcher_as_a_hook_starts_it(self):
        sh = find_sh()
        if not sh:
            self.skipTest("no POSIX shell")
        line = f'"{posix(os.path.join(PLUGIN, "hooks", "run-hook.cmd"))}" modules.py --self-test'
        out = subprocess.run([sh, "-c", line], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("workproof self-test ok: Python 3.", out.stdout)

    def test_modules_py_without_the_flag_prints_nothing(self):
        out = subprocess.run([sys.executable, "-B", os.path.join(HERE, "modules.py")], capture_output=True, text=True)
        self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""))


class Doctor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = scratch_plugin(self.tmp.name)
        if not find_sh():
            self.skipTest("no POSIX shell")

    def test_a_working_plugin_passes_every_check(self):
        code, text = report(self.root)
        self.assertEqual(code, 0, text)
        self.assertIn("the hook launcher starts: workproof self-test ok", text)
        self.assertIn("workproof-doctor: every check passed", text)
        self.assertNotIn("FAIL", text)

    def test_a_launcher_that_cannot_run_fails_with_its_fix(self):
        with open(os.path.join(self.root, "hooks", "run-hook.cmd"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(": << 'END'\n@echo run-hook: no Python 3 found on PATH 1>&2\n@exit /b 1\nEND\n"
                         "echo 'run-hook: no Python 3 found on PATH' >&2\nexit 1\n")
        code, text = report(self.root)
        self.assertEqual(code, 1, text)
        self.assertIn("FAIL  the hook launcher fails (exit 1): run-hook: no Python 3 found on PATH", text)
        self.assertIn("fix: install Python 3.10 or later", text)
        self.assertIn("1 check(s) failed", text)

    def test_a_missing_launcher_fails(self):
        os.remove(os.path.join(self.root, "hooks", "run-hook.cmd"))
        code, text = report(self.root)
        self.assertEqual(code, 1)
        self.assertIn("the hook launcher is missing", text)

    @unittest.skipIf(os.name == "nt", "Windows has no executable bit")
    def test_a_lost_executable_bit_fails_with_the_chmod_line(self):
        tool = os.path.join(self.root, "bin", "work-plan")
        os.chmod(tool, 0o644)
        code, text = report(self.root)
        self.assertEqual(code, 1, text)
        self.assertIn("not executable: " + os.path.join("bin", "work-plan"), text)
        self.assertIn('chmod +x "%s"' % tool, text)

    def test_an_old_python_fails_its_check(self):
        ok, what, fix = doctor.check_python((3, 9, 1))
        self.assertFalse(ok)
        self.assertIn("is too old", what)
        self.assertIn("3.10 or later", fix)

    def test_the_notes_show_the_modules_and_the_plans_root_this_process_sees(self):
        environ = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_PLUGIN_OPTION_", "WORKPROOF_", "PROOF_"))}
        code, text = report(self.root, dict(environ, CLAUDE_PLUGIN_OPTION_SECRET_GUARD="false",
                                            WORKPROOF_PLANS_ROOT=self.tmp.name))
        self.assertEqual(code, 0, text)
        self.assertIn("secret_guard off, proof_plans default (on)", text)
        self.assertIn("plans root: %s (from WORKPROOF_PLANS_ROOT)" % self.tmp.name, text)
        self.assertIn("plans root: none set", report(self.root, environ)[1])

    def test_the_command_on_path_runs_the_doctor(self):
        tool = posix(os.path.join(PLUGIN, "bin", "workproof-doctor"))
        out = subprocess.run([find_sh(), tool], capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("workproof-doctor: every check passed", out.stdout)


if __name__ == "__main__":
    unittest.main()
