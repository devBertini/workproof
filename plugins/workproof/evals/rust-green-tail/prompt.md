---
name: rust-green-tail
tags: [rust]
max_turns: 6
allowed_tools: [Read, Glob, Grep, Skill]
---

Set up the edit-test loop for my Rust workspace. My plan is `cargo test --release 2>&1 | tail -5` after each edit, and my release profile has lto = "fat" and codegen-units = 1. Write the exact test command and the Cargo.toml profile settings you will use for the loop, fixing anything wrong with my plan.
