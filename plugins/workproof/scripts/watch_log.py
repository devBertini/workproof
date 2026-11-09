#!/usr/bin/env python
"""Follow a run's log for the Monitor tool and end with the run (skill running-long-commands).

    python -B scripts/watch_log.py <log> [--events REGEX] [--end REGEX] [--pid PID] [--idle S] [--from-end]
        [--wait-file S] [--width N] [--interval S]

Prints each line of <log> that matches --events or --end (cut to --width characters), flushed at once, and exits as
soon as the watch has nothing left to say, so the Monitor ends with the run instead of at its own expiry:

- a line matching --end (default `DONE rc=`, the line every run's log ends with): printed, exit 0;
- --pid given and that process gone: what is left of the log is read first, then `WRITER EXITED pid N, no end line`,
  exit 4 (a crash, a kill, a run that never wrote its end line);
- the log has not grown for --idle seconds (default 1200, under the Monitor's 30-minute cap, so a run that died
  without its end line still ends the watch; 0 turns it off): `IDLE S s, no new line in <log>`, exit 5 (a hang);
- the log missing after --wait-file seconds (default 120): `NO LOG <log> after S s`, exit 3.

`tail -f <log> | grep | awk '/END/{exit}'` never does this: awk exits, but `tail` only learns it when it writes again,
and a log that goes quiet after its end line keeps the pipeline, and the Monitor, alive until the Monitor expires,
which wakes the session with a stale notice. Reads from the start of the log unless --from-end; a log truncated or
replaced is read again from its start. SIGTERM ends it quietly. Standard library only.
"""
import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import time


def alive(pid):
    """Whether the process runs. `os.kill(pid, 0)` on Windows sends CTRL_C_EVENT, so it never tests a process there:
    tasklist knows Windows ids, and a POSIX `sh` on PATH (Git Bash) knows the ids of its own `$!`."""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return not zombie(pid)
    listed = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True).stdout
    if f'"{pid}"'.encode() in listed:
        return True
    sh = shutil.which("sh")
    return sh is not None and subprocess.run([sh, "-c", f"kill -0 {pid}"], capture_output=True).returncode == 0


def zombie(pid):
    """Whether the process has exited and only waits for its parent to collect it: `kill -0` still succeeds for it,
    and a writer in that state will never write its end line. Linux says so in /proc; elsewhere `ps` does."""
    try:
        with open("/proc/%d/stat" % pid, encoding="utf-8", errors="replace") as handle:
            return handle.read().rpartition(")")[2].split()[:1] == ["Z"]
    except OSError:
        pass
    try:
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return state.strip().startswith("Z")


def native_path(path):
    """A Git Bash path (`/c/dir/...`) as Python opens it: the mod starts this script with no shell to convert it."""
    return re.sub(r"^/([A-Za-z])/", lambda m: m.group(1).upper() + ":/", path)


def emit(line, width):
    line = line.rstrip("\r\n")
    print(line[:width] if width > 0 else line, flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log")
    parser.add_argument("--events", default="")
    parser.add_argument("--end", default=r"DONE rc=")
    parser.add_argument("--pid", type=int)
    parser.add_argument("--idle", type=float, default=1200)
    parser.add_argument("--from-end", action="store_true")
    parser.add_argument("--wait-file", type=float, default=120)
    parser.add_argument("--width", type=int, default=240)
    parser.add_argument("--interval", type=float, default=0.5)
    args = parser.parse_args(argv)
    args.log = native_path(args.log)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    events = re.compile(args.events) if args.events else None
    end = re.compile(args.end)

    started = time.time()
    while not os.path.exists(args.log):
        if args.pid is not None and not alive(args.pid):
            print("WRITER EXITED pid %d, no log %s" % (args.pid, args.log), flush=True)
            return 4
        if time.time() - started >= args.wait_file:
            print("NO LOG %s after %g s" % (args.log, args.wait_file), flush=True)
            return 3
        time.sleep(args.interval)

    offset = os.path.getsize(args.log) if args.from_end else 0
    inode = os.stat(args.log).st_ino
    partial = b""
    grown_at = time.time()
    while True:
        writer_gone = args.pid is not None and not alive(args.pid)
        try:
            stat = os.stat(args.log)
        except FileNotFoundError:
            stat = None
        if stat is not None:
            if stat.st_ino != inode or stat.st_size < offset:
                inode, offset, partial = stat.st_ino, 0, b""
            if stat.st_size > offset:
                with open(args.log, "rb") as handle:
                    handle.seek(offset)
                    chunk = handle.read()
                offset += len(chunk)
                grown_at = time.time()
                partial += chunk
        lines = partial.split(b"\n")
        partial = lines.pop()
        if writer_gone and partial:
            lines.append(partial)
            partial = b""
        for line in (raw.decode("utf-8", "replace") for raw in lines):
            if end.search(line):
                emit(line, args.width)
                return 0
            if events is not None and events.search(line):
                emit(line, args.width)
        if writer_gone:
            print("WRITER EXITED pid %d, no end line" % args.pid, flush=True)
            return 4
        if args.idle > 0 and time.time() - grown_at >= args.idle:
            print("IDLE %g s, no new line in %s" % (args.idle, args.log), flush=True)
            return 5
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
