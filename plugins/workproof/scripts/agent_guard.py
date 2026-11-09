"""PreToolUse hook on Agent: keeps each of this plugin's agents on the model of its activity (agent_roles module).

- A call that asks one of this plugin's agents for a model above the agent's own default (haiku < sonnet < opus, any
  other model counted above opus) asks the user first.
- With the option refuse_inheriting_agents on (off by default; WORKPROOF_REFUSE_INHERITING_AGENTS=on also turns it on), a generic agent (general-purpose, claude, fork, Plan,
  or no type) and Explore are refused unless the call names haiku or sonnet: they run on the session's model, which
  is often the most expensive one.

Input: the hook JSON on stdin. Exit 0 lets the call run (with a JSON `ask` on stdout when the user must answer); exit 2
refuses it, with the reason on stderr for Claude. Unreadable input passes: this is a cost policy, not a security
boundary. Standard library.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from modules import module_enabled, option_on  # noqa: E402


def option_on_env(name):
    """The same switch from a plain variable, for settings files whose plugin options cannot be set per project."""
    return os.environ.get(name, "").strip().lower() in {"true", "1", "yes", "on"}

PLUGIN_ROOT = os.path.dirname(HERE)
AGENTS = os.path.join(PLUGIN_ROOT, "agents")
RANK = {"haiku": 0, "sonnet": 1, "opus": 2}
ABOVE_ALL = 3
GENERIC = {"", "general-purpose", "claude", "fork", "Plan"}
CHEAP = {"haiku", "sonnet"}


def plugin_name():
    """The plugin's name from its manifest, so the agent prefix follows a rename."""
    try:
        with open(os.path.join(PLUGIN_ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as handle:
            return json.load(handle)["name"]
    except (OSError, ValueError, KeyError):
        return "workproof"


def rank(model):
    model = (model or "").strip().lower()
    if not model or model == "inherit":
        return None
    return RANK.get(model, ABOVE_ALL)


def default_model(name):
    """The `model` of one of this plugin's agents, or None when the plugin has no such agent."""
    path = os.path.join(AGENTS, name + ".md")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as handle:
        found = re.search(r"^model:\s*([\w.-]+)\s*$", handle.read(), re.MULTILINE)
    return found.group(1) if found else None


def decide(tool_input, refuse_inheriting=False, prefix=None):
    """Returns (verdict, reason), verdict being allow, ask or deny."""
    prefix = (prefix or plugin_name()) + ":"
    kind = tool_input.get("subagent_type") or ""
    model = tool_input.get("model") or ""
    if refuse_inheriting and kind in GENERIC | {"Explore"} and model not in CHEAP:
        return "deny", ("{} runs on the session's model unless the call names a cheaper one. Pass model haiku or "
                        "sonnet, or use the {}* agent of the activity (skill {}delegating-to-agents)."
                        .format(kind or "An agent with no type", prefix, prefix))
    if not kind.startswith(prefix):
        return "allow", ""
    default = default_model(kind[len(prefix):])
    asked, own = rank(model), rank(default)
    if asked is not None and own is not None and asked > own:
        return "ask", "{} runs on {}; this call asks for {}. A model above an agent's default needs the user's yes.".format(
            kind, default, model)
    return "allow", ""


def main():
    if not module_enabled("agent_roles"):
        return 0
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    tool_input = data.get("tool_input") if isinstance(data, dict) else None
    if not isinstance(tool_input, dict):
        return 0
    refuse = option_on("refuse_inheriting_agents") or option_on_env("WORKPROOF_REFUSE_INHERITING_AGENTS")
    verdict, reason = decide(tool_input, refuse)
    if verdict == "deny":
        sys.stderr.write(reason + "\n")
        return 2
    if verdict == "ask":
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                                 "permissionDecisionReason": reason}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
