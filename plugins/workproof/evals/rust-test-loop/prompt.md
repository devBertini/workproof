---
name: rust-test-loop
tags: [rust]
max_turns: 6
allowed_tools: [Read, Glob, Grep, Skill]
---

Set up the edit-test loop for the crate `engine` of my Rust workspace, where cargo-nextest is installed. My plan is `cargo test --release 2>&1 | tail -5` after each edit, with lto = "fat" in the release profile; one physics test is slow unoptimized. Write the exact test command and the exact Cargo.toml lines, fixing what is wrong with my plan.
