# Templates

## SPEC (`docs/SPEC.md`; a game: `docs/GDD.md`)

    # <Product>: spec, version <N>
    ## Purpose                who it is for, the problem, what success looks like (three lines)
    ## Flows                  | ID | Flow | Criteria (When <event>, the <product> shall <response>) |
    ## Later                  what this version leaves out
    ## Look                   the picked direction and its identity file
    ## Data and architecture  the data model, the module or service boundaries, technology and why
    ## Budgets                | What | Number | Measured by |
    ## Threats                asset, entry point, threat, mitigation: one line each
    ## Risks                  each with its spike
    ## Slices                 slice: flow IDs, risk; the milestones marked

## Approval summary

The version's flows, one line each, and what stays out, in the language of whoever approves.

## Review brief

Review <commits or diff> of <project> against <SPEC flows or criteria> and the contract in stage <N> of its plan. Report only what breaks a criterion, a budget or a threat; skip style. Check that each criterion is met by working code, not a stub or a display-only control, and is proven by a test; check edge cases and error states; check the budgets <list> and the threats <list>. Read only: build nothing and run no test (the session's own runs hold the build folder).
