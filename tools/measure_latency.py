#!/usr/bin/env python
"""Measures how long one guard call takes, started the way Claude Code starts a command hook.

    measure_latency.py [calls] [--file]        (default 100; --file measures the file-tool guard)

Each call runs `sh -c '"<plugin>/hooks/run-hook.cmd" secret_guard.py'` (Git Bash on Windows) with a hook JSON on
stdin, so the time includes the shell, the launcher and the Python start. Half of the calls are allowed commands and
half are blocked ones. Prints the median, 95th percentile and maximum in milliseconds.
Python 3.9+, standard library only.
"""
import json
import os
import statistics
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests"))
from test_support import find_sh, posix  # noqa: E402

LAUNCHER = os.path.join(REPO, "plugins", "workproof", "hooks", "run-hook.cmd")
CASES = [("ls -la src && git status --short", 0), ("cat .env", 2)]
FILE_CASES = [("/work/app/src/main.py", 0), ("/work/app/id_rsa", 2)]


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))]


def main(argv):
    files = "--file" in argv
    argv = [a for a in argv if a != "--file"]
    calls = int(argv[1]) if len(argv) > 1 else 100
    sh = find_sh()
    if not sh:
        print("measure_latency: no POSIX shell found", file=sys.stderr)
        return 2
    line = f'"{posix(LAUNCHER)}" {"file_guard.py" if files else "secret_guard.py"}'
    times = []
    for i in range(calls):
        command, expected = (FILE_CASES if files else CASES)[i % len(CASES)]
        payload = json.dumps({"tool_name": "Read", "tool_input": {"file_path": command}} if files else
                             {"tool_name": "Bash", "tool_input": {"command": command}})
        start = time.perf_counter()
        result = subprocess.run([sh, "-c", line], input=payload, capture_output=True, text=True, timeout=60)
        times.append((time.perf_counter() - start) * 1000)
        if result.returncode != expected:
            print(f"measure_latency: unexpected exit {result.returncode} for case {i % len(CASES)}", file=sys.stderr)
            return 1
    print(f"calls: {calls}  median: {statistics.median(times):.0f} ms  "
          f"p95: {percentile(times, 0.95):.0f} ms  max: {max(times):.0f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
