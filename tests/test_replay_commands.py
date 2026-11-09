"""Tests of replay_commands.py on a small synthetic transcript: python -B test_replay_commands.py"""
import io
import json
import os
import sys
import tempfile
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
HERE = os.path.join(os.path.dirname(TESTS), "plugins", "workproof", "scripts")  # the engines under test
sys.path.insert(0, os.path.join(os.path.dirname(TESTS), "tools"))
import replay_commands  # noqa: E402


def call(tool, command):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": tool, "input": {"command": command}}]}}


class Replay(unittest.TestCase):
    def test_counts_commands_blocks_and_skips_other_tools(self):
        entries = [call("Bash", "ls"), call("Bash", "cat .env"), call("Bash", "cat .env"),
                   call("PowerShell", r"Get-Content .\.env"), call("Bash", "docker inspect db"),
                   call("Read", "cat .env"), {"type": "user", "message": {"content": "cat .env"}},
                   {"type": "assistant", "message": {"content": [{"type": "text", "text": "tool_use"}]}}]
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "a.jsonl"), "w", encoding="utf-8") as handle:
                handle.write("\n".join(json.dumps(e) for e in entries) + "\nnot json with tool_use\n")
            with open(os.path.join(folder, "skipme.jsonl"), "w", encoding="utf-8") as handle:
                handle.write(json.dumps(call("Bash", "cat .env")) + "\n")
            out = io.StringIO()
            replay_commands.replay([folder], skip=["skipme"], out=out)
        text = out.getvalue()
        self.assertIn("commands replayed: 5", text)
        self.assertIn("unique commands: 4", text)
        self.assertIn("blocked: 4 (3 unique)", text)
        self.assertIn("file tool calls replayed: 1", text)
        self.assertIn("file tool calls blocked: 0", text)

    def test_file_tool_calls_are_counted_apart(self):
        def use(tool, tool_input):
            return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": tool, "input": tool_input}]}}
        entries = [use("Read", {"file_path": "/w/README.md"}), use("Read", {"file_path": "/w/app/prod.pem"}),
                   use("Grep", {"pattern": "x", "glob": "**/*.key"}), use("Write", {"file_path": "/w/a.pem"})]
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "a.jsonl"), "w", encoding="utf-8") as handle:
                handle.write("\n".join(json.dumps(e) for e in entries) + "\n")
            out = io.StringIO()
            replay_commands.replay([folder], out=out)
        text = out.getvalue()
        self.assertIn("commands replayed: 0", text)
        self.assertIn("file tool calls replayed: 3", text)
        self.assertIn("file tool calls blocked: 2", text)
        self.assertNotIn(".env\n", text.replace("(environment file (.env))", ""))

    def test_the_report_holds_no_command_text(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "a.jsonl")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(call("Bash", "cp secret-name.pem /tmp/x")) + "\n")
            out = io.StringIO()
            replay_commands.replay([path], out=out)
        self.assertNotIn("secret-name", out.getvalue())


if __name__ == "__main__":
    unittest.main()
