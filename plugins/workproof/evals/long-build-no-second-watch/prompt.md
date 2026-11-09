---
name: long-build-no-second-watch
tags: [long-runs]
max_turns: 6
allowed_tools: [Read, Glob, Grep, Skill]
---

Following this plugin's method for long runs, you started my 20-minute test suite as a background command that writes its own log, and you called the watch_run tool on that log. Two questions, one line each: should you also start a Monitor on the log or look at it every minute? And when the completion notice arrives, what exactly do you read?
