---
name: closing-deliveries
description: "Closes a delivery: checks the order is done and proven, cleans the machine, updates documents, plan and commits, and writes the final report. Use before the final report of a delivery or session, and when the user says to stop, wrap up or that they are done for the day."
---

# Closing a delivery

If `${user_config.delivery}` is false, this module is off: ignore this skill.

Go through every step; one that does not apply is skipped, not faked.

1. **Everything ordered is done.** If not, keep working, or say exactly what is left and why.
2. **Proof:** every claim in the report has its command and passing result, or is reported as not verified.
3. **Machine clean,** as soon as each thing is spent and not only here:
   - No server, browser, watcher or background command of this session still running.
   - Containers: every stack this session started is down; throwaway stacks removed with the volumes and images they made (never the volumes of the project's own long-lived stack); build cache this session left, pruned.
   - Emulators and simulators this session created: shut down, or deleted when the user stops.
   - Scratch files and generated media deleted file by file on listed paths, never by a recursive delete of a folder you did not list.
   - Merged worktrees: `git worktree remove <path>`, then `git branch -d <branch>`.
4. **Documents:** the project's `CLAUDE.md` (state, new pitfalls, new decisions of the user) and the `docs/` the change touched.
5. **Instructions:** each correction from the user in this delivery and each gap a skill or rule had is written into its home now (skill `workproof:maintaining-rules`).
6. **Plan** (a project that keeps one; skip when the proof plans module is off or there is no plan): stages closed with their proof (skill `workproof:working-in-stages`). With a stage still open, its notes say where the work stopped and what comes next. With every stage closed, the next step goes into the project's `CLAUDE.md` first, then `work-plan finish`, which deletes the plan and its notes.
7. **Git:** `git status` shown; every proven stage committed on its own (`guarded-commit <repo> "<message>" <paths>`); anything left uncommitted is named with the reason. Nothing is pushed without the user's yes.
8. **Report:** what changed, what failed or is unverified, what you said earlier that proved wrong, which instructions changed and why, and what is left for the user, if anything.
9. **Fresh context:** when a milestone or the delivery closed, check that the next step is written where step 6 put it (the open stage's notes, or the project's `CLAUDE.md` when no plan is left) and suggest `/clear`; an open plan is printed back at the next session start.
10. **Last call, only if needed:** one question with the decisions and approvals still open.
