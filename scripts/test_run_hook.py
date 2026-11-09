"""Tests for run-hook.cmd, the launcher every plugin's hooks start through. Run: python -B test_run_hook.py

Builds a throwaway plugin (in a folder whose path has a space) holding a copy of the launcher and
a probe script, then starts the launcher the ways a hook starts it: through sh/bash (on Windows
Git Bash hands a .cmd to cmd.exe), through sh reading the file as a script (the macOS and Linux
part, run on every system), and on Windows through cmd.exe and PowerShell.

Contract: arguments after the launcher path are plain words. On Windows, cmd.exe /c drops the
outer quotes of a command line that holds more than one quoted part, so a quoted argument breaks
a launcher path that has a space."""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LAUNCHER = os.path.join(HERE, "run-hook.cmd")
REPO = os.path.dirname(HERE)
WINDOWS = os.name == "nt"

PROBE = r'''import json, sys
data = sys.stdin.read()
print(json.dumps({"args": sys.argv[1:], "stdin": data, "major": sys.version_info[0]}))
sys.stderr.write("probe stderr\n")
sys.exit(int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 0)
'''


def find_sh():
    """A POSIX shell; on Windows Git Bash, never System32's WSL launcher."""
    if WINDOWS:
        for path in (os.environ.get("CLAUDE_CODE_GIT_BASH_PATH"), shutil.which("bash"),
                     r"C:\Program Files\Git\bin\bash.exe"):
            if path and os.path.isfile(path) and "system32" not in path.lower():
                return path
        return None
    return shutil.which("sh")


class Launcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = os.path.join(cls.tmp.name, "my plugin")
        os.makedirs(os.path.join(cls.root, "hooks"))
        os.makedirs(os.path.join(cls.root, "scripts"))
        cls.launcher = os.path.join(cls.root, "hooks", "run-hook.cmd")
        shutil.copyfile(LAUNCHER, cls.launcher)
        os.chmod(cls.launcher, 0o755)
        with open(os.path.join(cls.root, "scripts", "probe.py"), "w", encoding="utf-8") as handle:
            handle.write(PROBE)
        cls.sh = find_sh()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def assert_probe(self, out, code=0, args=("2", "ab")):
        self.assertEqual(out.returncode, code, out.stderr)
        result = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(result["args"], list(args))
        self.assertEqual(result["stdin"], '{"tool_name": "Bash"}')
        self.assertEqual(result["major"], 3)
        self.assertIn("probe stderr", out.stderr)

    def run_cmd(self, argv):
        return subprocess.run(argv, input='{"tool_name": "Bash"}', capture_output=True, text=True, timeout=60)

    def test_hook_command_through_sh(self):
        """The hook command line as Claude Code runs it: sh -c (Git Bash on Windows)."""
        if not self.sh:
            self.skipTest("no POSIX shell")
        root = self.root.replace("\\", "/")
        command = f'"{root}/hooks/run-hook.cmd" probe.py 2 ab'
        self.assert_probe(self.run_cmd([self.sh, "-c", command]), code=2)

    def test_sh_part_on_every_system(self):
        if not self.sh:
            self.skipTest("no POSIX shell")
        out = self.run_cmd([self.sh, self.launcher.replace("\\", "/"), "probe.py", "2", "ab"])
        self.assert_probe(out, code=2)

    def test_exit_zero_passes_through(self):
        if not self.sh:
            self.skipTest("no POSIX shell")
        command = f'"{self.launcher.replace(chr(92), "/")}" probe.py ok'
        self.assert_probe(self.run_cmd([self.sh, "-c", command]), code=0, args=("ok",))

    @unittest.skipUnless(WINDOWS, "cmd.exe only on Windows")
    def test_cmd_part(self):
        out = self.run_cmd(["cmd.exe", "/d", "/c", self.launcher, "probe.py", "2", "ab"])
        self.assert_probe(out, code=2)

    @unittest.skipUnless(WINDOWS, "PowerShell hook shell only on Windows")
    def test_powershell_hook_shell(self):
        command = f"& '{self.launcher}' probe.py 2 ab; exit $LASTEXITCODE"
        out = self.run_cmd(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command])
        self.assert_probe(out, code=2)

    def test_no_python_on_path(self):
        if not self.sh:
            self.skipTest("no POSIX shell")
        empty = os.path.join(self.tmp.name, "empty")
        os.makedirs(empty, exist_ok=True)
        env = dict(os.environ, PATH=empty)
        if WINDOWS:
            env["PATH"] = empty + os.pathsep + os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
            argv = ["cmd.exe", "/d", "/c", self.launcher, "probe.py"]
        else:
            argv = [self.sh, self.launcher, "probe.py"]
        out = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("no Python 3 found", out.stderr)


class Copies(unittest.TestCase):
    def test_every_plugin_carries_the_same_launcher(self):
        with open(LAUNCHER, "rb") as handle:
            canonical = handle.read()
        self.assertNotIn(b"\r\n", canonical, "the launcher must keep LF line endings for sh")
        copies = glob.glob(os.path.join(REPO, "plugins", "*", "hooks", "run-hook.cmd"))
        for copy in copies:
            with self.subTest(copy=copy), open(copy, "rb") as handle:
                self.assertEqual(handle.read(), canonical, "copy differs from scripts/run-hook.cmd")


if __name__ == "__main__":
    unittest.main()
