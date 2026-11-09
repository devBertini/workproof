"""Tests for file_guard.py, the secret guard of the file tools. Run: python -B test_file_guard.py"""
import json
import os
import subprocess
import sys
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
HERE = os.path.join(os.path.dirname(TESTS), "plugins", "workproof", "scripts")  # the engines under test
sys.path.insert(0, HERE)
import file_guard  # noqa: E402
import secret_patterns  # noqa: E402
from test_support import find_sh, posix  # noqa: E402

PLUGIN = os.path.dirname(HERE)

BLOCKED = [
    ("Read", {"file_path": "/work/app/.env"}),
    ("Read", {"file_path": "C:\\work\\app\\.env.local"}),
    ("Read", {"file_path": "deploy/prod.env"}),
    ("Read", {"file_path": "/home/me/.ssh/id_ed25519"}),
    ("Read", {"file_path": "/home/me/.aws/credentials"}),
    ("Read", {"file_path": "certs/server.key", "offset": 1, "limit": 5}),
    ("Edit", {"file_path": "/work/app/.env", "old_string": "A=1", "new_string": "A=2"}),
    ("NotebookEdit", {"notebook_path": "/work/keys/service_account_prod.json", "new_source": "x"}),
    ("Grep", {"pattern": "DATABASE_URL", "path": "/work/app/.env"}),
    ("Grep", {"pattern": "KEY", "path": "/work/app", "glob": ".env*"}),
    ("Grep", {"pattern": "KEY", "glob": "**/.env"}),
    ("Grep", {"pattern": "BEGIN", "glob": "**/*.pem"}),
    ("Grep", {"pattern": "K", "glob": "**/.env*"}),
    ("Grep", {"pattern": "K", "glob": "*/config/.env.*"}),
    ("Grep", {"pattern": "K", "glob": "**/id_rsa*"}),
    ("Grep", {"pattern": "BEGIN", "glob": "*.{pem,key}"}),
    ("Grep", {"pattern": "x", "glob": "config/.env.*", "output_mode": "count"}),
]

ALLOWED = [
    ("Read", {"file_path": "/work/app/.env.example"}),
    ("Read", {"file_path": "/work/app/.env.sample"}),
    ("Read", {"file_path": "/work/app/src/env.ts"}),
    ("Read", {"file_path": "/work/app/README.md"}),
    ("Read", {"file_path": "/work/app/docs/environment.md"}),
    ("Edit", {"file_path": "/work/app/.gitignore", "old_string": "a", "new_string": ".env"}),
    ("Edit", {"file_path": "/work/app/.env.example", "old_string": "A=", "new_string": "A=1"}),
    ("Grep", {"pattern": "\\.env", "path": "/work/app"}),
    ("Grep", {"pattern": "id_rsa", "glob": "*.md"}),
    ("Grep", {"pattern": "process.env", "glob": "**/*.{ts,tsx}"}),
    ("Grep", {"pattern": "x", "glob": "*.json"}),
    ("Grep", {"pattern": "x", "glob": "**/environment*.md"}),
    ("Grep", {"pattern": "x"}),
    ("Write", {"file_path": "/work/app/.env", "content": "A=1"}),
    ("Glob", {"pattern": "**/.env*"}),
    ("Bash", {"command": "cat .env"}),
    ("Read", {}),
    ("Read", {"file_path": 7}),
    ("Read", "not an object"),
]


def payload(tool, tool_input):
    return json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input})


def run_hook(text, **env):
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_PLUGIN_OPTION_", "WORKPROOF_"))}
    return subprocess.run([sys.executable, "-B", os.path.join(HERE, "file_guard.py")], input=text,
                          capture_output=True, text=True, env=dict(clean, **env), timeout=60)


class Verdicts(unittest.TestCase):
    def test_blocked(self):
        for tool, tool_input in BLOCKED:
            with self.subTest(tool=tool, tool_input=tool_input):
                self.assertIsNotNone(file_guard.verdict(tool, tool_input))

    def test_allowed(self):
        for tool, tool_input in ALLOWED:
            with self.subTest(tool=tool, tool_input=tool_input):
                self.assertIsNone(file_guard.verdict(tool, tool_input))

    def test_the_reason_names_the_tool_the_file_and_its_kind(self):
        reason = file_guard.verdict("Read", {"file_path": "/work/app/.env"})
        self.assertIn("Read tool", reason)
        self.assertIn("`/work/app/.env`", reason)
        self.assertIn("environment file", reason)

    def test_braces_expand(self):
        self.assertEqual(file_guard.expand_braces("*.{pem,key}"), ["*.pem", "*.key"])
        self.assertEqual(file_guard.expand_braces("a.txt"), ["a.txt"])
        self.assertEqual(file_guard.expand_braces("{a,b}/{c,d}"), ["a/c", "a/d", "b/c", "b/d"])

    def test_extra_names_apply(self):
        names = secret_patterns.compile_names([(secret_patterns.glob_to_regex("*.vault"), "x")])
        self.assertIsNotNone(file_guard.verdict("Read", {"file_path": "/w/prod.vault"}, names))
        self.assertIsNotNone(file_guard.verdict("Grep", {"pattern": "x", "glob": "**/*.vault"}, names))
        self.assertIsNone(file_guard.verdict("Read", {"file_path": "/w/prod.vault"}))


class HookEntry(unittest.TestCase):
    def test_a_block_exits_2_with_the_reason_on_stderr(self):
        out = run_hook(payload("Read", {"file_path": "/work/app/.env"}))
        self.assertEqual(out.returncode, 2)
        self.assertIn("Blocked by workproof (secret-guard module)", out.stderr)
        self.assertEqual(out.stdout, "")

    def test_an_allowed_call_exits_0_silently(self):
        out = run_hook(payload("Read", {"file_path": "/work/app/README.md"}))
        self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""))

    def test_bad_input_passes(self):
        for text in ("", "not json", "[]", '{"tool_name": "Read"}'):
            with self.subTest(text=text):
                self.assertEqual(run_hook(text).returncode, 0)

    def test_the_module_switch_turns_it_off(self):
        blocked = payload("Read", {"file_path": "/work/app/.env"})
        self.assertEqual(run_hook(blocked, CLAUDE_PLUGIN_OPTION_SECRET_GUARD="false").returncode, 0)
        self.assertEqual(run_hook(blocked, CLAUDE_PLUGIN_OPTION_SECRET_GUARD="true").returncode, 2)

    def test_the_users_extra_names_reach_the_hook(self):
        call = payload("Read", {"file_path": "/w/prod.vault"})
        self.assertEqual(run_hook(call).returncode, 0)
        self.assertEqual(run_hook(call, CLAUDE_PLUGIN_OPTION_EXTRA_SECRET_NAMES="*.vault").returncode, 2)
        self.assertEqual(run_hook(call, WORKPROOF_EXTRA_SECRET_NAMES="*.vault").returncode, 2)


class Launcher(unittest.TestCase):
    """The hook command as Claude Code runs it: through a POSIX shell (Git Bash on Windows) and the launcher."""

    def run_launcher(self, text):
        sh = find_sh()
        if not sh:
            self.skipTest("no POSIX shell")
        line = f'"{posix(os.path.join(PLUGIN, "hooks", "run-hook.cmd"))}" file_guard.py'
        return subprocess.run([sh, "-c", line], input=text, capture_output=True, text=True, timeout=60)

    def test_block_and_allow_through_the_launcher(self):
        self.assertEqual(self.run_launcher(payload("Read", {"file_path": "C:\\app\\.env"})).returncode, 2)
        self.assertEqual(self.run_launcher(payload("Read", {"file_path": "C:\\app\\main.py"})).returncode, 0)

    def test_the_hooks_file_runs_it_on_the_file_tools_and_fails_closed(self):
        with open(os.path.join(PLUGIN, "hooks", "hooks.json"), encoding="utf-8") as handle:
            entries = json.load(handle)["hooks"]["PreToolUse"]
        entry = next(e for e in entries if "file_guard.py" in e["hooks"][0]["command"])
        self.assertEqual(sorted(entry["matcher"].split("|")), sorted(file_guard.PATH_FIELDS))
        hook = entry["hooks"][0]
        self.assertEqual(hook["command"], '"${CLAUDE_PLUGIN_ROOT}/hooks/run-hook.cmd" file_guard.py')
        self.assertEqual(hook["onFailure"], "block")
        self.assertLessEqual(hook["timeout"], 10)


if __name__ == "__main__":
    unittest.main()
