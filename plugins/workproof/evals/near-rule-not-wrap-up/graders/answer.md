---
type: llm
---

PASS if the answer names a file Claude loads as instructions for this project (the project's CLAUDE.md, or a `.claude/rules/` file scoped to TypeScript files) and gives one short imperative rule line with no date, no author and no history (no 'the user said on ...', no 'decided on'). That it could not write the file itself does not matter. FAIL if it suggests only remembering it, a dated log entry, or a separate notes file.
