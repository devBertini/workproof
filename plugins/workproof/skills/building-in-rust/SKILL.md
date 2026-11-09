---
name: building-in-rust
description: Builds and tests a Rust workspace with a short unit loop - the test command and how to read a run by its own exit code, profiles that keep tests fast, optimized builds for runs that compute, and the traps of replacing a running program. Use when building, testing or profiling a Rust project, or setting up its edit-test loop or Cargo profiles.
---

# Building in Rust

If `${user_config.rust}` is false, this module is off: ignore this skill.

- The tools live in `~/.cargo/bin`, which a tool shell may not have on PATH: start with `export PATH="$HOME/.cargo/bin:$PATH"` when `cargo` is not found.
- Tests: `cargo nextest run -p <crate>` for the reached cases, `cargo nextest run --workspace` for the whole suite (plain `cargo test` when nextest is not installed), read by its own exit code with the full log kept in a file: a pipe into `tail` hides a red. Doc tests need `cargo test --doc`.
- Keep `dev` and test profiles fast: a compute-heavy test sets `opt-level` on its crate through `[profile.dev.package.<crate>]`. Fat LTO, one codegen unit and `panic=abort` belong to a separate release or install profile, never to the test loop.
- A run that computes (a simulation, a benchmark, a search) is built optimized, and independent seeds or cases run on every core.
- A build or suite over a minute runs in the background with its own log (skill `workproof:running-long-commands`).
- Profile before changing a slow run.
- A running executable cannot be replaced on Windows: stop it before a build that writes the same path, or install to another folder.
- A project with a flow map proves its flows with Rust tests (skill `workproof:mapping-flows`).
- `target/` is ignored by git; remove it when the project's work ends and disk is short.
