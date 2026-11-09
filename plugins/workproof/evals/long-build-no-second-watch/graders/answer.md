---
type: llm
---

PASS if the answer says no to both a Monitor on the same log and periodic polling AND says that at the end it reads the `DONE rc=` line (the exit code the log ends with) and only the failing lines of the log (for example by grep), not the whole log. FAIL if it starts a Monitor, polls, or reads the whole log or just its tail.
