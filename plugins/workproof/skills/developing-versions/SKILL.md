---
name: developing-versions
description: Runs a version of any product (app, game, service, site) from spec to release in thin slices - a short spec and a round with the product's decision maker, the foundation, one slice per stage (contract, core, screens, proof, close), reviews only where they pay, time budgets for test loops, triage of failures and notes, and a hardening pass sized to the version. Use when a product or version starts, a slice is planned, built or closed, notes arrive mid-version, a test loop is slow, or before declaring a version finished.
---

If `${user_config.flow_map}` is false, this module is off: ignore this skill.

# Developing versions

Ship the smallest useful version with quality, then improve it version by version. Every step, check and document must pay for its time; leave out what does not.

## When it applies

- A new product, or a version: a release that was ordered (1.0, an update). Version 1 holds the main flow and what it needs; the rest goes to the SPEC's "Later" list.
- A version's stages go into the project's open plan, or into a new plan once that one is finished (skill `workproof:working-in-stages`; `work-plan new` refuses while a plan exists). A one-step fix needs no stage.

## Where facts live

| Fact | Home |
|---|---|
| Purpose; the version's flows and their criteria; "Later"; data model and architecture boundaries; technology and why; budgets; threats; risks; slice order with each slice's risk and the milestones | `docs/SPEC.md` (a game: `docs/GDD.md`): current truth only, a page or two per version |
| A criterion | one testable sentence: "When <event>, the <product> shall <response>" |
| Why a decision was taken | `docs/DECISIONS.md`, one line each |

An existing spec or plan document is not rewritten up front; it shrinks as each slice corrects the part it touches. The plan, the flow map and open findings keep their homes (skills `workproof:working-in-stages` and `workproof:mapping-flows`). Templates: `references/templates.md`.

## Phase 0. Spec

1. One short round with whoever decides the product: what it does, its main flow, the version's scope. A new product, or a version that changes the look, also picks from two or three rendered directions of the main screen.
2. The SPEC decides now only what is expensive to change later: the look, the data model, the architecture boundaries, the budgets. The rest is decided in its slice. Budgets are three to five numbers the product can break (launch, a hang, frame time, p95 latency, data at scale); threats take a few lines, sized to the product.
3. A risky product (accounts, money, personal data stored or shared off the device, a security boundary) gets one independent review of the SPEC by a reader who did not write it.
4. The decision maker approves a short summary: the flows and what is out.

## Phase 1. Foundation

- One command builds and one runs the tests; one green test of each kind the project uses.
- The project's instructions hold its three loop budgets (below).
- The version's flows enter the map before their code (skill `workproof:mapping-flows`, then `check-flows --from-cache`).
- The look's tokens and base components; the main flow's thinnest path through every layer, running.
- A spike only for a real unknown; a scale fixture or benchmark only for a budget the product can break.
- An existing product only adds its flows, its spikes and any changed tokens.

## Phase 2. One slice per stage

1. **Contract.** Title: the slice, its flow IDs and its risk. Risky: it touches accounts, money, personal data leaving the device, a security boundary, a stored-data migration or concurrency; small: copy or layout only; normal: the rest. Done-when: "F-... `validated` and `check-flows --check` exits 0; the budgets the slice reaches, by their test". Test names live only in the map.
2. **Core first:** rules, data, services. Tests first where the behavior is known.
3. **Screens** over the core: every state the flow reaches, every language the product ships, the look, labels on new controls.
4. **Proof:** the reached tests and the flow check for the change (skill `workproof:mapping-flows`); each proven flow's `Entry` cell names its files; captures looked at.
5. **Close:** the SPEC corrected if the product changed; the plan text the slice used up deleted; `work-plan done` with the review dispositions and who found each defect in `--result`; the commit.

A slice starts from a green main; after another lane merges, the flows that merge reached run first.

- **A slice's proof is short:** unit, property and loopback tests that finish in seconds, written with the code. Runs that take minutes (soak, leak and sustained-rate runs, long latency or throughput measurements, fuzzing) belong to hardening, never to a slice's done-when; the exception is a spike whose measurement picks the architecture. The same holds for repetition: "N runs in a row" of a suite or of the whole app is a hardening run, done once. A flaky test is fixed at its cause (a timing, an order or a shared resource) and proven by a short test that forces that cause, or by repeating only that test in seconds; the repeated full-suite run waits for hardening. A defect such a run finds outside the current flow joins hardening unless it blocks the next slice.
- **After a merge,** run the tests the merge reaches (the changed packages and those that depend on them); the whole suite runs at each milestone.

## Reviews: only where they pay

- **A risky slice:** one independent review of its contract and core, started in the background once the core's tests are green, while the screens go on; its findings become `[defect]` stages.
- **Everything else:** one pass per milestone over the slices since the last pass; at hardening, one over what no pass covered.
- **The brief:** `references/templates.md`. The reviewer reads only: it builds nothing and runs no test, since the session's runs hold the build folder and the machine.
- A review pass whose findings were all declined or style for two versions is cut at the retrospective.

## Time budgets

- Wall clock, build included. Targets: the reached unit cases after an edit under 30 s; a slice's reached UI flows under 5 minutes; the gate before a release or a merge under 10 minutes. A loop already over its target keeps its measured time as its budget until a stage brings it down.
- A loop that grows past its budget becomes a `[defect]` stage, taken right after the current slice: profile it first, then apply the remedy.

## Triage

- **A failure:** a core test red means the core. Core green and a UI test red: reproduce it at the core's boundary with the UI's input (a unit test, or a route test for a service); red there means the core lacked a test and both get fixed; green means the screens. All green but wrong on the real target: a missing test, written at the lowest level that shows the defect.
- **A note from the decision maker** becomes a stage tagged with its class: `[defect]` fixed now, with a test that failed before; `[change]` updates the SPEC and reopens or adds a slice; `[refinement]` (look, wording, feel) built at once when it fits one build, otherwise kept for hardening; `[idea]` goes to "Later" and enters only on their word, with what it displaces said.

## Phase 3. Hardening, sized to the version

- **Version 1 or a store release:** the full runs the project asks for at a finish; budgets measured on the real target; the review pass, with the threats across the whole surface in its brief; dependencies and secrets; accessibility, languages, privacy and store rules.
- **An update:** the same, limited to what the update reached.
- **Gate:** no flow `broken`, `revalidate` or `no proof`; each `device only` flow walked on the real device; open findings empty or each accepted by the decision maker; their walk of the build.

## Phase 4. Learn

The closing report gives stages by tag, defects by who found them, findings per review pass and the slowest loop against its budget. What found nothing is cut and what let a defect through is tightened.
