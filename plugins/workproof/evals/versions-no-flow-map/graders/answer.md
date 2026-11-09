---
type: llm
---

PASS if the answer says the SPEC's own flow table (the flows listed in docs/SPEC.md) is the list of flows AND that each slice's done-when names the test command that proves its flows (instead of `check-flows`). FAIL if it insists a flow map must be created first, or names neither.
