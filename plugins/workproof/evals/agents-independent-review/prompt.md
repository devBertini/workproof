---
name: agents-independent-review
tags: [agents]
max_turns: 6
allowed_tools: [Read, Glob, Grep, Skill]
---

Start an independent review of my change to the login code before I merge it. My idea: a general-purpose subagent on opus, with my summary of why the change is correct pasted in as the brief. Tools that start agents are off in this session, so write out the exact agent call you will make (agent type, model, and the brief itself), fixing anything wrong with my idea.
