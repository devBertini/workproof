"""Tests for agent_guard.py. Run: python -B test_agent_guard.py"""
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agent_guard  # noqa: E402

P = agent_guard.plugin_name() + ":"
AGENTS = ("implementer", "runner", "reviewer", "debugger", "designer")


def run(tool_input, **env):
    full = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_PLUGIN_OPTION_", "WORKPROOF_"))}
    full.update(env)
    return subprocess.run([sys.executable, "-B", os.path.join(HERE, "agent_guard.py")],
                          input=json.dumps({"tool_name": "Agent", "tool_input": tool_input}),
                          capture_output=True, text=True, env=full, timeout=60)


class Decide(unittest.TestCase):
    def test_every_agent_has_a_model(self):
        for name in AGENTS:
            self.assertIn(agent_guard.default_model(name), agent_guard.RANK, name)

    def test_defaults_pass(self):
        for name in AGENTS:
            self.assertEqual(agent_guard.decide({"subagent_type": P + name})[0], "allow", name)

    def test_effort_is_free(self):
        self.assertEqual(agent_guard.decide({"subagent_type": P + "implementer", "effort": "high"})[0], "allow")

    def test_above_default_asks(self):
        for name, model in (("implementer", "opus"), ("runner", "sonnet"), ("runner", "opus"),
                            ("reviewer", "some-future-model"), ("implementer", "claude-opus-9")):
            self.assertEqual(agent_guard.decide({"subagent_type": P + name, "model": model})[0], "ask", (name, model))

    def test_at_or_below_default_passes(self):
        for name, model in (("implementer", "sonnet"), ("implementer", "haiku"), ("reviewer", "opus"),
                            ("reviewer", "sonnet"), ("implementer", "inherit")):
            self.assertEqual(agent_guard.decide({"subagent_type": P + name, "model": model})[0], "allow", (name, model))

    def test_generic_passes_by_default(self):
        for kind in ("general-purpose", "claude", "fork", "Plan", "", "Explore"):
            self.assertEqual(agent_guard.decide({"subagent_type": kind})[0], "allow", kind)

    def test_generic_refused_with_the_option(self):
        for kind in ("general-purpose", "claude", "fork", "Plan", "", "Explore"):
            self.assertEqual(agent_guard.decide({"subagent_type": kind}, True)[0], "deny", kind)
            self.assertEqual(agent_guard.decide({"subagent_type": kind, "model": "opus"}, True)[0], "deny", kind)
            for model in ("haiku", "sonnet"):
                self.assertEqual(agent_guard.decide({"subagent_type": kind, "model": model}, True)[0], "allow")
        self.assertEqual(agent_guard.decide({}, True)[0], "deny")

    def test_other_agents_pass(self):
        for kind in ("claude-code-guide", "statusline-setup", "other-plugin:implementer"):
            self.assertEqual(agent_guard.decide({"subagent_type": kind, "model": "opus"}, True)[0], "allow", kind)


class Hook(unittest.TestCase):
    def test_deny_exits_2_with_reason(self):
        out = run({"subagent_type": "general-purpose"}, CLAUDE_PLUGIN_OPTION_REFUSE_INHERITING_AGENTS="true")
        self.assertEqual(out.returncode, 2)
        self.assertIn("delegating-to-agents", out.stderr)

    def test_plain_variable_turns_the_refusal_on(self):
        out = run({"subagent_type": "Explore"}, WORKPROOF_REFUSE_INHERITING_AGENTS="on")
        self.assertEqual(out.returncode, 2)

    def test_generic_passes_without_the_option(self):
        out = run({"subagent_type": "general-purpose"})
        self.assertEqual((out.returncode, out.stdout), (0, ""))

    def test_ask_prints_the_decision(self):
        out = run({"subagent_type": P + "implementer", "model": "opus"})
        self.assertEqual(out.returncode, 0)
        self.assertEqual(json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"], "ask")

    def test_allow_is_silent(self):
        out = run({"subagent_type": P + "implementer", "effort": "high"})
        self.assertEqual((out.returncode, out.stdout), (0, ""))

    def test_module_off_passes_everything(self):
        out = run({"subagent_type": P + "implementer", "model": "opus"}, CLAUDE_PLUGIN_OPTION_AGENT_ROLES="false",
                  CLAUDE_PLUGIN_OPTION_REFUSE_INHERITING_AGENTS="true")
        self.assertEqual((out.returncode, out.stdout), (0, ""))

    def test_bad_input_passes(self):
        for text in ("not json", "[]", '{"tool_input": 3}'):
            out = subprocess.run([sys.executable, "-B", os.path.join(HERE, "agent_guard.py")], input=text,
                                 capture_output=True, text=True, timeout=60)
            self.assertEqual(out.returncode, 0, text)


if __name__ == "__main__":
    unittest.main()
