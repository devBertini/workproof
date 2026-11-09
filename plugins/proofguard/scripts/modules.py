"""Module switches of the proofguard plugin.

Each module (secret_guard, proof_plans, agent_roles and the rest) is a boolean plugin option, on by default. Claude Code exports an option to
hook processes as CLAUDE_PLUGIN_OPTION_<KEY>; an unset, empty or unknown value leaves the module on, and only an
explicit false, 0, no or off turns it off. Standard library.
"""
import os

OFF = {"false", "0", "no", "off"}


def module_enabled(key, environ=None):
    value = (environ if environ is not None else os.environ).get("CLAUDE_PLUGIN_OPTION_" + key.upper(), "")
    return value.strip().lower() not in OFF


ON = {"true", "1", "yes", "on"}


def option_on(key, environ=None):
    """An option that is off by default: only an explicit true, 1, yes or on turns it on."""
    value = (environ if environ is not None else os.environ).get("CLAUDE_PLUGIN_OPTION_" + key.upper(), "")
    return value.strip().lower() in ON
