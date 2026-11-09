---
name: rules-move-a-failing-closed-hook
tags: [delivery]
max_turns: 6
allowed_tools: [Read, Glob, Grep, Skill]
---

I maintain a Claude Code plugin from a local folder; its PreToolUse hook is set to block when it fails. I want to move the plugin's source folder to another path while two sessions are open. In what order do I do the move, the marketplace change and the reload, and why? Three sentences at most.
