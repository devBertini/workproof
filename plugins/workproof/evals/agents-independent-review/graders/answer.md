---
type: llm
---

PASS if the answer uses this plugin's dedicated reviewer agent (`workproof:reviewer` or 'the reviewer agent') rather than a general-purpose one AND says the brief gives the code or diff, not the author's own conclusion or summary of why it is correct. FAIL if it keeps the general-purpose agent or pastes the author's reasoning as the brief.
