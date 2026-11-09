---
name: rust-green-tail
tags: [rust]
max_turns: 6
allowed_tools: [Read, Glob, Grep, Skill]
---

In my Rust workspace I run `cargo test --release 2>&1 | tail -5` after each edit and my release profile has lto = "fat" and codegen-units = 1. The tail looked fine so I assume tests pass. Anything wrong with this loop? Be brief.
