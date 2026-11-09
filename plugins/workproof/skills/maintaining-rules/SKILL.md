---
name: maintaining-rules
description: "Keeps a project's instructions for Claude right: where each rule lives (CLAUDE.md, path-scoped rules, skills, hooks, permission rules) and how it is written and proven. Use before editing a CLAUDE.md, a .claude/rules file, a skill, an agent, a hook or a settings file, and when the user corrects or decides something (\"from now on\", \"always\", \"never\")."
---

# Maintaining rules

If `${user_config.delivery}` is false, this module is off: ignore this skill.

## Where each thing lives

| What | Home |
|---|---|
| Holds in every session of every project | the user's `~/.claude/CLAUDE.md`, or a CLAUDE.md in a folder above the projects |
| One project's facts: commands, pitfalls, decisions, state | the project's `CLAUDE.md`; history and detail in its `docs/` |
| An instruction for one part of the code (a folder, a file type) | `.claude/rules/<topic>.md` with `paths:` globs; it loads when Claude reads a matching file |
| A procedure or trap of a subject | a skill (`skills/<name>/SKILL.md`) in the project or a plugin |
| A rule with zero exceptions | a permission rule in settings or a hook, never only text |
| How replies read (length, format) | an output style; the reply language is the `language` setting |

One home per fact; other places point to it.

## How to write

- Only what Claude cannot work out from the repository and what changes behavior. Imperative, with the reason when it is not obvious.
- No author, date, history or quotation ("was X, now Y", "decided on"): every loaded word is paid for in each session, and git keeps who and when. A date stays only where it changes what Claude does (a deadline, an expiry); records in `docs/` keep theirs.
- Replace old text; never append a log. A CLAUDE.md stays short (under 200 lines); a longer one moves code-area instructions into path-scoped rules.
- A skill: a gerund name (`reviewing-migrations`, `running-benchmarks`); a third-person description that says what it does, then "Use when..." with its triggers (under 1,024 characters); `SKILL.md` under 500 lines, detail in `references/` one level deep; scripts with tests beside them.
- A new skill is written when real work needs it, not ahead of time.

## After a change

- Prove it: `claude plugin validate <plugin folder>` for a plugin, the tests of any changed script (`claude plugin test <plugin folder>` for a hooks module), and a fresh session (or `/reload-plugins`) that shows the skill or hook loaded. A changed skill of a plugin that has an eval suite reruns its cases (`claude plugin eval <plugin folder> --case <name>`), which bills the account for each run.
- A hook that fails closed blocks every matching tool call while its script is missing: move or rename a plugin's source folder only after pointing its marketplace at the new path, then run `/reload-plugins` in each open session.
- Commit the change on its own, with a message that names the rule, never mixed into feature work.
