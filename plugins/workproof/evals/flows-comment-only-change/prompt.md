---
name: flows-comment-only-change
tags: [flow-map]
max_turns: 6
allowed_tools: [Read, Glob, Grep, Skill]
---

A flow in my docs/FLOWS.md dropped to `revalidate` because I fixed a typo in a comment of a file its Entry cell cites. No test changed, and the report of the last run that holds every proof of that flow is still on disk. Commands are off in this session, so give the cheapest correct engine command to get the flow back, and why no test needs to run.
