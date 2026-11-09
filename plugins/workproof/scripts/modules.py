"""Module switches of the workproof plugin.

Each module (secret_guard, proof_plans, agent_roles and the rest) is a boolean plugin option, on by default. Claude
Code exports an option to hook processes as CLAUDE_PLUGIN_OPTION_<KEY>; an unset, empty or unknown value leaves the
module on, and only an explicit false, 0, no or off turns it off.

`modules.py --self-test` is what the session-start self-test and workproof-doctor start through the hook launcher: it
exits 0 with one line when the launcher found a Python new enough for the hooks, 1 with the reason otherwise.
Standard library.
"""
import os
import sys

MINIMUM = (3, 9)
OFF = {"false", "0", "no", "off"}


def module_enabled(key, environ=None):
    value = (environ if environ is not None else os.environ).get("CLAUDE_PLUGIN_OPTION_" + key.upper(), "")
    return value.strip().lower() not in OFF


ON = {"true", "1", "yes", "on"}


def option_on(key, environ=None):
    """An option that is off by default: only an explicit true, 1, yes or on turns it on."""
    value = (environ if environ is not None else os.environ).get("CLAUDE_PLUGIN_OPTION_" + key.upper(), "")
    return value.strip().lower() in ON


def self_test(version=None, out=None):
    """0 and one line when this Python can run the hooks; 1 and the reason when it is too old."""
    version = sys.version_info if version is None else version
    found = "Python %d.%d" % (version[0], version[1])
    if tuple(version[:2]) < MINIMUM:
        print("workproof self-test: %s is too old, the hooks need %d.%d or later" % ((found,) + MINIMUM),
              file=out or sys.stderr)
        return 1
    print("workproof self-test ok: " + found, file=out or sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(self_test() if sys.argv[1:] == ["--self-test"] else 0)
