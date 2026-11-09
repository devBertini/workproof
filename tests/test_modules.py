"""Tests for the module switches. Run: python -B test_modules.py"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
HERE = os.path.join(os.path.dirname(TESTS), "plugins", "workproof", "scripts")  # the engines under test
sys.path.insert(0, HERE)
from modules import module_enabled  # noqa: E402

SECRET_READ = {"tool_name": "Bash", "tool_input": {"command": "python -c \"print(open('.env').read())\""}}


def run(script, payload, **env):
    full = dict(os.environ, **env)
    return subprocess.run([sys.executable, "-B", os.path.join(HERE, script), *(["hook"] if script == "work_plan.py" else [])],
                          input=json.dumps(payload), capture_output=True, text=True, env=full, timeout=60)


class Switch(unittest.TestCase):
    def test_values(self):
        for value in ("", "true", "1", "yes", "on", "anything"):
            self.assertTrue(module_enabled("secret_guard", {"CLAUDE_PLUGIN_OPTION_SECRET_GUARD": value}), value)
        for value in ("false", "0", "no", "off", " FALSE "):
            self.assertFalse(module_enabled("secret_guard", {"CLAUDE_PLUGIN_OPTION_SECRET_GUARD": value}), value)
        self.assertTrue(module_enabled("proof_plans", {}))


class Hooks(unittest.TestCase):
    def test_secret_guard_on_blocks_and_off_allows(self):
        self.assertEqual(run("secret_guard.py", SECRET_READ).returncode, 2)
        self.assertEqual(run("secret_guard.py", SECRET_READ, CLAUDE_PLUGIN_OPTION_SECRET_GUARD="true").returncode, 2)
        self.assertEqual(run("secret_guard.py", SECRET_READ, CLAUDE_PLUGIN_OPTION_SECRET_GUARD="false").returncode, 0)

    def test_proof_plans_off_prints_nothing(self):
        with tempfile.TemporaryDirectory() as project:
            os.makedirs(os.path.join(project, ".git"))
            os.makedirs(os.path.join(project, "docs"))
            subprocess.run([sys.executable, "-B", os.path.join(HERE, "work_plan.py"), "new", "--goal", "demo goal",
                            "--where", project], check=True, capture_output=True)
            payload = {"hook_event_name": "SessionStart", "source": "startup", "cwd": project, "session_id": "t1"}
            on = run("work_plan.py", payload, WORKPROOF_PLANS_ROOT="", CLAUDE_PLUGIN_OPTION_PLANS_ROOT="")
            self.assertEqual(on.returncode, 0, on.stderr)
            self.assertIn("demo goal", on.stdout)
            off = run("work_plan.py", payload, CLAUDE_PLUGIN_OPTION_PROOF_PLANS="false")
            self.assertEqual((off.returncode, off.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
