---
name: running-long-commands
description: "Runs a command that takes over a minute without spending wakes: a background shell command that writes its own log and ends it with its exit code, plus the deferred watch_run tool, which wakes the session at most once more (first fatal line, a hang, a missing log). Use when starting or planning a build, test suite, benchmark, conversion or any other run expected to pass a minute."
---

If `${user_config.long_runs}` is false, this module is off: ignore this skill.

# Running long commands

Every wake rereads the whole conversation, and the cost grows with its length, so a long run should wake the session once at its end and at most once more on trouble.

1. Start it as a background Bash command (`run_in_background`) that writes its own log to a scratch or ignored build folder and ends it with its exit code; the completion notice is the wake that reads the result:

   ```
   ( <command> ; echo "DONE rc=$?" ) > "<log>" 2>&1
   ```

2. Beside it, call the tool `mcp__workproof__watch_run` (deferred: load it with ToolSearch `select:mcp__workproof__watch_run`) with the log's absolute path, a `fatal` pattern and, if the run prints them, `progress` lines for the status line. It wakes the session at most once: at the first `fatal` line, after `idle` seconds without a new line (default 1200), or when the log never appears. The run's end is not its wake. Starting patterns, to adapt to the tool's own output:

   - `cargo build`, `cargo nextest`: fatal `^error(\[E\d+\])?:|panicked at|^\s+FAIL `, progress `^\s+(PASS|Compiling) `
   - `docker compose build` or a test profile: fatal `^ERROR|failed to solve|exited with code [1-9]| FAIL `, progress `^#\d+ \[`
   - `pytest`: fatal `^(FAILED|ERROR) |Traceback`, progress `PASSED`

3. While it runs, do independent work; do not poll the log and start no Monitor beside it.

4. At the completion notice read the `DONE rc=` line and the log's failing lines (`grep -n -E '<fatal>' "<log>" | head`), never the whole log.

Without the tool (the module failed to load, or mods are off in this session) the Monitor tool can follow the log with the plugin's `watch-log` command: `watch-log "<log>" --events '<fatal>' --end 'DONE rc='` emits one event per fatal line and exits with the run. An agent has no Monitor and no watch_run: it runs the command in the foreground with a bound (`timeout`). Stop a run by its PID, never by a pattern that could match other sessions' runs.
