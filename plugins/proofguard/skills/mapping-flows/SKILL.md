---
name: mapping-flows
description: Keeps a project's flow map (docs/FLOWS.md) true with the check-flows engine - the cycle of a change, what each status means and why only the engine writes it, what a flow may cite as proof, how to choose what to rerun, the modes and exit codes, merging maps from parallel worktrees, and scripts/flows.json. Use when a project has a flow map, before changing its code, when deciding what to retest, before running the flow check, or when adding, renaming or splitting a flow or a test the map cites.
---

If `${user_config.flow_map}` is false, this module is off: ignore this skill.

# Mapping flows

A project with a map holds `docs/FLOWS.md` (the map), `docs/.flows-state.json` (what runs proved, written by the engine) and `scripts/flows.json` (its configuration). Run the engine with `check-flows <mode>` (on the Bash PATH while this plugin is enabled) from inside the project, or with `--project <root>`; `--help` lists the modes. A map in Brazilian Portuguese (`docs/FLUXOS.md`, statuses `validado`, `revalidar`, `sem prova`, `so em aparelho`, `quebrado`, scoreboard `placar`) works unchanged: the engine reads the language from the map and writes it back in the same language. A new map is started with `check-flows --init` (`--language pt-BR` for Portuguese).

## The cycle

1. Understand the change; open the map and list the flows it reaches. A new flow goes into the map before its code.
2. Implement, then prove: a path a person walks by touching the screen needs a UI test; a pure rule (calculation, formatting, parsing, scheduling) needs a unit test.
3. Run the engine; it writes the statuses. New work is done only when its proof ran green.

For work of more than one step, keep the stages in a plan (skill `proofguard:working-in-stages`) and close a stage on the engine's command, for example `work-plan done N --bundle -- check-flows --check`.

## Statuses

Only the engine writes a status, from an actual run, with its date: `validated`, `revalidate`, `no proof`, `device only`, `broken` (words, never symbols). The scoreboard between `<!-- scoreboard:start -->` and `<!-- scoreboard:end -->` is rewritten by the engine, never typed.

- `revalidate`: a file the flow's `Entry` cell cites changed. The note names every cited file, not only the changed ones (find the change with `git log` on them). It does not mean the change reaches the proof: compare what each proof asserts with the changed lines first. A change no assertion can observe (accessibility sizes, a look no test reads) reruns nothing, and the report names those flows. Comments and blank lines also demote (the fingerprint is the bytes): `--reuse` the last report that holds every proof of each such flow.
- `no proof`: no proof found. A lookup failure is never reported as a test failure.
- `device only`: the flow cites `device:<reason>`, meaning automating it is impossible (a real camera, a real purchase); a debt counted apart, not a pass.
- `broken`: a proof failed; a cited test is no longer declared in the source (a rename breaks its flow, so test names the map cites are not renamed); a case selected by name is absent from the run's report; `device:` sits beside another proof or its reason is under 12 characters; or the flow mixes a UI and a unit proof where `refuse_mixed_ui_unit` is true.

## Proofs (the `Proof` cell, separated by `<br>`)

- A test: XCTest `Class/method()`; Swift Testing `Suite name/Test name` (the suite is the `struct` it sits in); a bare case name when unique; `Target/Suite/case` for a `swift test` package; in a Rust workspace `<binary id>/<module path::test>` (`my-core/face::tests::blink`, an integration test `my-core::parity/outer`); with test runners that write JUnit XML `<test file stem>/<describe > ... > test>`.
- `print:<path>`: a committed screenshot, for a flow that is only what it looks like.
- `manual:YYYY-MM-DD:<who>`: checked by hand on a device; expires after 90 days; a future date is broken.
- `device:<reason>`: above.

## The map

One flow per row: `| ID | Flow | Entry | Proof | Status | Validated on |`. The engine reads the first four cells and rewrites the last two. An ID is `F-`, capital letters, a dash and digits (`F-ACC-01`); a bad or duplicate ID stops the engine with exit 2 before anything is written. `Entry` names the source files the flow goes through; their bytes are its fingerprint, so each must exist in a folder the configuration indexes (`cited file not found` demotes the flow; a name found in two folders prints a clash and the first folder wins).

## Choosing what to run

- The retest scope comes from the map, never from memory.
- A change to documents, IDs or proof names: `--from-cache` (no test, no device).
- `--only-stale` only when the change reaches every demoted flow; otherwise run every proof of each reached flow by name in one run with the project's own test command and `--reuse` that report.
- `--reuse` judges a flow only from reports that hold every one of its proofs; with only some, it stores their results and keeps the status (`partly covered by the bundle, status kept`). A case failed in any report given counts as failed, so after fixing one case of a multi-proof flow, rerun all its proofs in one run and pass that report alone. Several reports go side by side, never merged with `xcresulttool merge`.
- `--check` verifies and writes nothing: exit 1 on a `broken` flow or a typed Status cell.
- An iOS run needs a simulator for its device kind: `devices` in `scripts/flows.json`, or `--device <name or UDID>` for one run. The engine never creates or shuts down a simulator.

Exit codes: 0 clean; 1 a flow is `broken`, a batch gave no result, or `--check` found a difference; 2 unusable input, nothing written. The run modes (`--from-cache`, `--reuse`, `--only-stale`, `--changed`, `--resume`, `--prune-state`) do not combine.

## Parallel worktrees

Each worktree changes the map and `.flows-state.json`, so their merges conflict. Resolve row by row: a row both sides hold takes the side that changed it from the merge base (`git show :1:<path>`); a row both sides changed is checked by hand; IDs added on both sides can collide, and one is renumbered. Then `--reuse` a report that holds the cited tests.

## Configuration and a new map

The keys of `scripts/flows.json` and the steps of a new map: `references/configuration.md` (load it when writing or changing the configuration or a map).
