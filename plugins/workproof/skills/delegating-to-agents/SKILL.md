---
name: delegating-to-agents
description: Decides whether to hand work to an agent and, when it does, picks this plugin's agent of the activity (reviewer, debugger, implementer, designer, runner) with its model and effort, writes the brief and checks the result before it counts. Use before starting any agent or subagent (an independent review, a parallel task, a research run), and before deciding whether to delegate at all.
---

# Delegating to agents

If `${user_config.agent_roles}` is false, this module is off: ignore this skill.

## Delegate or not

Do the work yourself unless one of these holds:
- **An independent review** or a reader without the author's bias (the brief gives the code or result, never your conclusion).
- **Independent work that saves real waiting:** two or more parts that share no file, device, port or build folder.

Not a reason: a lookup you can do in a few tool calls, a check you could run inline, "to be safe". Each agent starts with no context, and its summary drops detail on the way back.

## Agents of this plugin

| Work | Agent | Default model, effort |
|---|---|---|
| Independent review, adversarial verification, a reader without bias (edits nothing) | `reviewer` | opus, high |
| A failure whose cause is unknown: reproduce, fix at the cause, prove | `debugger` | opus, high |
| Decided work to a written spec: edits, routine code and tests, a document, a measurement run to a written list | `implementer` | sonnet, medium |
| Design or taste: architecture, a plan, a game design, an art or audio direction, store text | `designer` | opus, medium |
| Mechanical: a known suite or script, a search, an extraction, a sweep, a summary with quotes; facts gathered from the web to a written question list, each with its address | `runner` | haiku, medium |

- The activity picks the agent and the agent's default is the model: never choose a model by the size or importance of the work. Decided work to a written spec is `implementer`, however long. `designer` only when the deliverable is a proposal judged by taste or architecture; gathering facts or numbers for a decision is research, not design. Research that needs the web goes to `runner` (facts) or `designer` (a proposal built on them): `implementer`, `reviewer` and `debugger` have no web tools.
- Raise effort before the model: `high` for a risky slice (a security boundary, cryptography, privilege, concurrency across processes) or after medium fell short; `xhigh` for the hardest long problem after high fell short. Work with no choice left (renames, sweeps, formatting, running a known suite) goes to `runner`.
- A `model` above the agent's default is only for work that fell short at high effort on the default; the plugin's hook asks the user before such a call runs.
- Generic agents (`general-purpose`, `fork`, `Plan`) and `Explore` run on the session's model unless the call names one: pass `model: haiku` for a search (`sonnet` when it must judge). With the option `refuse_inheriting_agents` on (currently `${user_config.refuse_inheriting_agents}`), the hook refuses them without a cheaper model.

## The brief

The agent knows only its brief: the user's order and this agent's part of it; the project folder; the files it may touch and those it must not; the skills to load (a `runner` has no Skill tool and reads them with Read); the proof that ends the work (a command and its expected result); what is already ruled out.

A run ends at the agent's `maxTurns` (160 for `implementer` and `debugger`, 100 for the others). Size the brief to fit:
- One plan stage per agent; two stages go to agents in sequence.
- The agent proves what it reached with short tests; long suites run after the merge.
- Output cut to the result lines; each rebuild and rerun spends turns.
- A status file the agent keeps, so a stopped run loses nothing. A run that stops at its cap is resumed with `SendMessage`, which keeps its context, not restarted.
- Parallel agents never share a file: give each its own files, or its own worktree, and merge shared manifests yourself.

## After it returns

Its result counts only after you read it and rerun its proof. Agents never commit, push or publish; you commit after review.
