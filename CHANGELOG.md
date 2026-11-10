# Changelog

## 1.0.1

- New plugin `workproof-skills` in the same marketplace (its own version 1.0.0): six skills and five agents of workproof that need nothing installed, built from the workproof sources, for Anthropic's plugin directory.
- The README says what the session-start hook reads and that it changes nothing in Claude Code's settings, permissions, memory or instructions.
- `.gitattributes` keeps the launcher, scripts and `bin/` wrappers byte for byte as committed (`-text`) instead of converting line endings on checkout.

## 1.0.0

First public release.

- Secret guard: a hook on Bash and PowerShell commands that blocks reads of secret files, environment dumps and Docker commands that print secrets; a hook on the Read, Grep, Edit and NotebookEdit tools that blocks them on a secret file; `guarded-commit`, which refuses secret file names and token formats in what it commits.
- Proof plans: `work-plan` and `verify-run`; a session-start hook that prints the project's plan; optional compaction of the conversation when a stage closes.
- Agent roles: five agents by activity, and a hook that asks before one runs on a model above its default.
- Commit rules for `guarded-commit`, turned on by environment variables.
- Flow map: the `check-flows` engine for Xcode, Swift package, Vitest, Playwright and nextest projects, with maps in English or Brazilian Portuguese.
- Long runs: the `watch_run` tool and the `watch-log` command.
- A session-start self-test and `workproof-doctor`, which say why the hooks cannot start and how to fix it.
- Runs on Python 3.9 or later, the version macOS ships.
- Skills for working in stages, developing versions, mapping flows, closing deliveries, maintaining rules, delegating to agents, Docker Compose systems, Rust, button layout and long commands; a `concise` output style.
