#!/usr/bin/env python
"""Builds plugins/workproof-skills, the skills-only edition for Anthropic's plugin directory, from workproof.

    python -B tools/build_skills_edition.py [--check]

The directory takes no command hooks, mods or bin/ files from a publisher that is not a Verified organization, so the
edition carries only the skills that need no engine of workproof, and its agents, as plain text. workproof stays the
single source: every edition file is generated here, each edit is an exact replacement that must find its text (a
changed source fails the build instead of shipping a stale edit), and the result may name no engine, hook or option.

--check builds into memory and exits 1 when the committed edition differs, without writing anything.
Python 3.9+, standard library only.
"""
import argparse
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(REPO, "plugins", "workproof")
EDITION = os.path.join(REPO, "plugins", "workproof-skills")
NAME = "workproof-skills"

SKILLS = ("building-in-rust", "closing-deliveries", "delegating-to-agents", "developing-docker-systems",
          "laying-out-buttons", "maintaining-rules")
AGENTS = ("debugger", "designer", "implementer", "reviewer", "runner")

MODULE_GATE = re.compile(r"If `\$\{user_config\.[a-z_]+\}` is false, this module is off: ignore this skill\.\n\n")

# (file, old, new): each old text must appear exactly once in the source file.
EDITS = (
    ("skills/building-in-rust/SKILL.md", " (skill `workproof:running-long-commands`)", ""),
    ("skills/building-in-rust/SKILL.md",
     "- A project with a flow map (`docs/FLOWS.md`) proves its flows with Rust tests (skill `workproof:mapping-flows`);"
     " without one, this line does not apply.\n", ""),
    ("skills/closing-deliveries/SKILL.md",
     "6. **Plan** (a project that keeps one; skip when the proof plans module is off or there is no plan): stages closed"
     " with their proof (skill `workproof:working-in-stages`). With a stage still open, its notes say where the work"
     " stopped and what comes next. With every stage closed, the next step goes into the project's `CLAUDE.md` first,"
     " then `work-plan finish`, which deletes the plan and its notes.",
     "6. **Plan** (a project that keeps one): stages closed with their proof. With a stage still open, the plan says"
     " where the work stopped and what comes next. With every stage closed, the next step goes into the project's"
     " `CLAUDE.md`."),
    ("skills/closing-deliveries/SKILL.md", " (`guarded-commit <repo> \"<message>\" <paths>`)",
     " (that stage's paths only, after checking that no secret file or token is staged)"),
    ("skills/delegating-to-agents/SKILL.md", "; the plugin's hook asks the user before such a call runs.",
     "; ask the user before such a call runs."),
    ("skills/delegating-to-agents/SKILL.md",
     " With the option `refuse_inheriting_agents` on (currently `${user_config.refuse_inheriting_agents}`), the hook"
     " refuses them without a cheaper model.", ""),
    ("skills/developing-docker-systems/SKILL.md", " (skill `workproof:running-long-commands`)",
     ", with its own log and its exit code read"),
    ("skills/developing-docker-systems/SKILL.md",
     "The Docker commands that print secrets, and what to run instead, are in skill `workproof:guarding-secrets`; with"
     " the secret guard on (`${user_config.secret_guard}`) its hook blocks them. A block means the command would print"
     " a secret: change the command, never work around the hook.",
     "Never run a Docker command that prints a secret's value (`docker compose config` or `docker inspect` on a stack"
     " with real secrets, `env` or `printenv` inside a container, `cat` of a file under `/run/secrets`): check that a"
     " value is set without printing it."),
    ("skills/laying-out-buttons/SKILL.md",
     "When `${user_config.buttons_positive_trailing_everywhere}` is true, Windows follows the trailing rule too:",
     "When the user's instructions put confirming actions trailing on Windows too, Windows follows the trailing rule:"),
    ("skills/laying-out-buttons/SKILL.md", " When it is false, follow the platform.", " Otherwise, follow the platform."),
    ("agents/runner.md",
     "; a literal `${user_config...}` in one is a placeholder only the Skill tool fills, so read its module as on", ""),
)

# Nothing in the edition may name an engine, a hook of the plugin, an option or a skill it does not carry.
FORBIDDEN = re.compile(r"\$\{user_config|\$\{CLAUDE_PLUGIN|work-plan|verify-run|guarded-commit|check-flows|watch-log"
                       r"|watch_run|workproof-doctor|the plugin's hook|workproof:")

PLUGIN_JSON = {
    "name": NAME,
    "displayName": "Workproof Skills",
    "version": "1.0.0",
    "description": "Skills and agents from Workproof that need nothing installed: delivery closing with proof,"
                   " keeping CLAUDE.md files and rules right, Docker Compose systems tested in throwaway stacks, a"
                   " short Rust build and test loop, button layout per platform, and five agents by activity with"
                   " model limits. Plain text: no hooks, no commands, no network.",
    "author": {"name": "devBertini"},
    "homepage": "https://github.com/devBertini/workproof",
    "repository": "https://github.com/devBertini/workproof",
    "documentationUrl": "https://github.com/devBertini/workproof/tree/main/plugins/workproof-skills#readme",
    "supportUrl": "https://github.com/devBertini/workproof/issues",
    "icon": "./.claude-plugin/icon.png",
    "license": "MIT",
    "keywords": ["delivery", "docker", "rust", "agents", "buttons", "claude-md", "workflow"],
}

README = """# Workproof Skills

Six skills and five agents from [Workproof](https://github.com/devBertini/workproof), as plain text. They need nothing installed: no hooks, no commands, no scripts, and nothing is sent anywhere. Install the full Workproof plugin from its own marketplace when you also want the secret guard, plans whose stages close only on a passing command, the flow map and the long-run watcher.

## Skills

| Skill | Use it when |
|---|---|
| `closing-deliveries` | Before the final report of a delivery: checks the order is done and proven, cleans the machine, updates documents and commits, then writes the report. |
| `maintaining-rules` | Before editing a CLAUDE.md, a rule, a skill, an agent, a hook or a settings file, and when you correct or decide something. |
| `developing-docker-systems` | Building, changing, testing or tearing down a Docker Compose system: each change proven in a throwaway project, lean images. |
| `building-in-rust` | Building, testing or profiling a Rust workspace with a short loop and test profiles. |
| `laying-out-buttons` | Building or reviewing a screen, dialog, form or game menu: button order per platform, one prominent action, hit targets. |
| `delegating-to-agents` | Before starting any agent: whether to delegate, which agent, its brief and how to check its result. |

## Agents

| Agent | Model | Does |
|---|---|---|
| `implementer` | sonnet | Carries out decided work to a written spec. |
| `reviewer` | opus | Reviews work it did not write and reports each defect with evidence, editing nothing. |
| `debugger` | opus | Finds the cause of a failure nobody has explained yet, fixes it and proves the fix. |
| `designer` | opus | Drafts work judged by taste or with product risk, as options to decide. |
| `runner` | haiku | Runs mechanical work with a checkable result. |

The agents load in Claude Code and Cowork; the skills load everywhere Claude runs.

## Try it

- "Close this delivery." (`closing-deliveries`)
- "Add a Postgres service to this compose file and prove it." (`developing-docker-systems`)
- "Review this change with an agent that did not write it." (`delegating-to-agents`, `reviewer`)

## What it runs, reads, writes and sends

Nothing on its own. The plugin is text that Claude reads when a skill fits; any command comes from Claude in your session, under your permission settings.

## Install

In Claude Code, from Workproof's own marketplace:

```
claude plugin marketplace add devBertini/workproof
claude plugin install workproof-skills@workproof
```

Install either this plugin or `workproof`, not both: `workproof` already carries these skills and agents.

## Support

Issues and questions: https://github.com/devBertini/workproof/issues

## License

MIT. See `LICENSE`.
"""


def frontmatter_agents_model(text):
    match = re.search(r"^model: (\S+)$", text, re.M)
    return match.group(1) if match else None


def build():
    """Returns {relative path: bytes} for the whole edition."""
    out = {}
    pending = list(EDITS)
    sources = [f"skills/{s}/SKILL.md" for s in SKILLS] + [f"agents/{a}.md" for a in AGENTS]
    for rel in sources:
        with open(os.path.join(SOURCE, rel), encoding="utf-8", newline="") as f:
            text = f.read().replace("\r\n", "\n")  # a Windows checkout may convert line endings
        text, gates = MODULE_GATE.subn("", text)
        if rel.startswith("skills/") and gates != 1:
            raise SystemExit(f"{rel}: expected one module gate line, found {gates}")
        for edit in [e for e in pending if e[0] == rel]:
            count = text.count(edit[1])
            if count != 1:
                raise SystemExit(f"{rel}: an edit's text appears {count} times, not once: {edit[1][:70]!r}")
            text = text.replace(edit[1], edit[2])
            pending.remove(edit)
        text = text.replace("workproof:", f"{NAME}:")
        bad = FORBIDDEN.findall(text.replace(f"{NAME}:", ""))
        if bad:
            raise SystemExit(f"{rel}: the edition still names {sorted(set(bad))}")
        for cited in re.findall(rf"{NAME}:([a-z-]+)", text):
            if cited not in SKILLS and cited not in AGENTS:
                raise SystemExit(f"{rel}: cites {NAME}:{cited}, which the edition does not carry")
        out[rel] = text.encode("utf-8")
    if pending:
        raise SystemExit(f"edits for files not in the edition: {sorted({e[0] for e in pending})}")
    for agent in AGENTS:
        model = frontmatter_agents_model(out[f"agents/{agent}.md"].decode("utf-8"))
        if f"| `{agent}` | {model} |" not in README:
            raise SystemExit(f"README: the row of agent {agent} does not name its model {model}")
    out[".claude-plugin/plugin.json"] = (json.dumps(PLUGIN_JSON, indent=2) + "\n").encode("utf-8")
    out["README.md"] = README.encode("utf-8")
    for rel in ("LICENSE", ".claude-plugin/icon.png"):
        with open(os.path.join(SOURCE, rel), "rb") as f:
            out[rel] = f.read()
    return out


def lf(data):
    """Bytes with CRLF read as LF, so a Windows checkout of the edition compares equal to what the build makes."""
    return None if data is None else data.replace(b"\r\n", b"\n")


def on_disk():
    found = {}
    for folder, _, files in os.walk(EDITION):
        for name in files:
            path = os.path.join(folder, name)
            with open(path, "rb") as f:
                found[os.path.relpath(path, EDITION).replace("\\", "/")] = f.read()
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when the edition on disk differs")
    args = parser.parse_args(argv)
    wanted = build()
    if args.check:
        have = on_disk()
        diff = sorted(p for p in set(wanted) | set(have) if lf(wanted.get(p)) != lf(have.get(p)))
        if diff:
            print("plugins/workproof-skills is out of date; run python -B tools/build_skills_edition.py:")
            for path in diff:
                print(f"  {path}")
            return 1
        print(f"plugins/workproof-skills matches its sources ({len(wanted)} files)")
        return 0
    for rel, data in wanted.items():
        path = os.path.join(EDITION, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
    for rel in set(on_disk()) - set(wanted):
        os.remove(os.path.join(EDITION, rel))
    print(f"wrote plugins/workproof-skills ({len(wanted)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
