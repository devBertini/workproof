# Workproof Skills

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
