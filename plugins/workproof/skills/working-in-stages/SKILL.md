---
name: working-in-stages
description: "Runs multi-step work from the project's plan (docs/ACTIVE-PLAN.json, changed only with the work-plan command): stages whose done-when names their proof, each closed on a command that passes. Use before starting multi-step work, when adding a stage, and when a session start, a compaction or /clear prints an active plan."
---

# Working in stages

If `${user_config.proof_plans}` is false, this module is off: ignore this skill.

The project's plan lives at `docs/ACTIVE-PLAN.json` in its git top-level folder. Change it only with `work-plan` (on the Bash PATH while this plugin is enabled), run from inside the project or with `--where <project folder>`:

```
work-plan new --goal "<the order, quoted as given>"
work-plan add --title "<stage>" --done-when "<acceptance and the proof that shows it>"
work-plan start N
work-plan note N "<decision, file, failing test>"
work-plan done N --result "<what changed>" --bundle -- <proof command...>   # runs it; closes only on exit 0
work-plan done N --result "<...>" --proof "<a proof that is not a command>"   # nothing runs: shown as [unverified]
work-plan pause N --reason "<what it waits on>"
work-plan drop N --reason "<why it is dropped>"
work-plan show
work-plan finish                                                            # only once every stage is closed
```

## The cycle

1. **Split the order into stages a reader can check.** Each stage gets a done-when that names its proof: a test command, a build, a capture, a file to read. If you cannot name the proof, the stage is too vague; split it or sharpen it. Keep the proof short (seconds to a few minutes): soak runs, long measurements and "N runs in a row" of a suite or of the whole product run once, when the version is hardened before release, never as a stage's proof. A flaky test is fixed at its cause (a timing, an order or a shared resource) and proven by a short test that forces that cause, or by repeating only that test in seconds.
2. **Start one stage.** Only one stage is in progress at a time; `pause` it (with the reason) before starting another.
3. **Write a note the moment something is decided or learned**, not at the end. After a compaction or a new session the plan and its notes are all that is left of the stage.
4. **Close a stage on a named command that passes.** `done --bundle -- <command>` runs the command from the project folder, and the stage closes only if it exits 0, storing the command, exit code, duration, a hash of the whole output and its last lines as evidence. If the command fails, times out or is refused, the stage stays open and the output tells you why. The command runs without holding the plan, so notes and stages added meanwhile are kept; if the stage was paused or dropped meanwhile it is not closed. `--proof` is only for a proof that is not a command (a capture read, a device check): nothing is run or checked, the text is taken as typed, and the stage is shown as `[unverified]`; say exactly what was checked.
5. **Never close on "should work".** A stage that cannot finish stays open with a note saying where it stopped and why.
6. **Resume from the printed plan.** At session start (and after `/clear` or a compaction) the plan is printed back: read the stage marked `[now]` and its notes, re-load any skills it needs, and continue. `[verified]` marks a stage closed through `--bundle`; `[unverified]` marks one closed with `--proof` alone.

`done` and `drop` erase the stage's notes: put what must outlast the stage into `--result` or into the project's documents. Stages are only ever marked, never removed. `finish` deletes the plan once every stage is closed, so move what lasts into the project's documents first.

## Notes

- The bundle command refuses outward or destructive commands (`git push`, a recursive `rm`, and any word the project lists in the variable `WORKPROOF_BUNDLE_REFUSE`): run those yourself, with the approval they need, and close with `--proof` (the stage then shows as `[unverified]`).
- A pipeline given to `--bundle` fails when any stage fails; wrap it as `-- bash -c '<pipeline>'`.
- A plan longer than its stages (research, design) belongs in a document that the goal cites; a product version's stages follow skill `workproof:developing-versions`.
- With the option `compact_on_stage_close` on (currently `${user_config.compact_on_stage_close}`), a main-session turn that ends after `work-plan done`, with the plan's repository committed, a stage pending and none started, is compacted at its end. Before ending such a turn, `note` into the next stage what it needs that only the conversation holds.
- On resume, invoke again the skills the `[now]` stage needs: a compaction brings invoked skills back only in part.
- `finish` once every stage is closed and skill `workproof:closing-deliveries` has moved what lasts into the project's documents.
