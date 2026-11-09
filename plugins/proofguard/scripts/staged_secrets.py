#!/usr/bin/env python
"""Secret formats in the staged content of a git repository.

    staged_secrets.py <repo>

Reads every staged file as the index holds it (the blob a commit would take, never the working copy) and applies
SECRET_CONTENT from secret_patterns.py. Every text blob is scanned whatever its size: it is streamed from git in
chunks, so memory stays bounded. UTF-16 and UTF-32 text (a byte order mark, or BOM-less UTF-16 recognised by its
NUL bytes, which is what Windows PowerShell 5.1 writes) is decoded before the scan. Only real binary content (a NUL
byte in the first 8 KiB that is not UTF-16/UTF-32 text) is skipped, with a note on stderr. stdout: one
`file:line: kind` line per FAIL finding; the matched value is never printed. stderr: notes and WARN findings.
Exit 0 clean, 1 a FAIL finding, 2 the check itself could not run (a refusal, never a pass).
Python 3.10+, standard library only.
"""
import bisect
import codecs
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import secret_patterns  # noqa: E402

GITLINK = "160000"
SNIFF_BYTES = 8192  # how much of a blob decides text from binary
CHUNK_BYTES = 1024 * 1024  # bytes read from git at a time
# Characters kept from the end of one window and put in front of the next, so a match that straddles a chunk
# boundary is seen whole. 4096 is far above the longest match of a bounded pattern (a fine-grained GitHub token,
# 11 + 255 = 266 characters) and of a private key header line. A pattern with no upper bound (an API key of
# `{40,}` characters) needs no more: only its start must lie in the window, and a match longer than the overlap
# is accepted at the chunk end without waiting for the next window (see scan_windows).
OVERLAP_CHARS = 4096


def git_env():
    """The environment for a git call: the caller's, without the settings that make git refuse literal pathspecs."""
    drop = ("GIT_GLOB_PATHSPECS", "GIT_NOGLOB_PATHSPECS", "GIT_ICASE_PATHSPECS")
    return {key: value for key, value in os.environ.items() if key not in drop}


def git(repo, *args):
    # --literal-pathspecs: no argument is ever read as a glob or pathspec magic (`:/`, `:(glob)`, `[1]`, `*`).
    return subprocess.run(["git", "--literal-pathspecs", "-C", repo, *args], capture_output=True, check=True,
                          env=git_env()).stdout


def staged_blobs(repo):
    """(path, blob sha, mode) of every added, copied, modified or renamed staged path; deletions hold no content."""
    raw = git(repo, "diff", "--cached", "--raw", "-z", "--no-renames", "--diff-filter=ACMR").split(b"\0")
    entries = []
    for i in range(0, len(raw) - 1, 2):
        meta = raw[i].decode().split()  # :oldmode newmode oldsha newsha status
        entries.append((raw[i + 1].decode("utf-8", "surrogateescape"), meta[3], meta[1]))
    return entries


def detect_encoding(head):
    """The codec that reads a blob whose first bytes are `head`, or None for binary content."""
    if head.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return "utf-32"
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    if b"\0" not in head:
        return "utf-8"
    # BOM-less UTF-16: ASCII-range text puts a NUL in every other byte (the high byte), none in the low byte.
    sample = head[:len(head) // 2 * 2]
    if len(sample) >= 4:
        even_nuls = sample[0::2].count(0) / (len(sample) // 2)
        odd_nuls = sample[1::2].count(0) / (len(sample) // 2)
        if odd_nuls >= 0.4 and even_nuls <= 0.1:
            return "utf-16-le"
        if even_nuls >= 0.4 and odd_nuls <= 0.1:
            return "utf-16-be"
    return None


def scan_windows(pieces, patterns, path, overlap=None):
    """Findings (level, text) over an iterable of decoded text pieces, scanned in overlapping windows.

    The line number is counted across the whole text. A match found twice (inside an overlap) is reported once.
    """
    overlap = OVERLAP_CHARS if overlap is None else overlap
    findings, seen = [], set()
    carry, carry_lines, truncated = "", 0, False

    def scan(window, final):
        newlines = None
        for level, kind, pattern in patterns:
            for match in pattern.finditer(window):
                if match.start() == 0 and truncated:
                    continue  # the window's first character has lost its left context; an earlier window decided it
                if not final and match.end() == len(window) and match.start() > len(window) - overlap:
                    continue  # touches the end and starts inside the next window: it may continue, judge it there
                if newlines is None:
                    newlines = [m.start() for m in re.finditer("\n", window)]
                line = carry_lines + bisect.bisect_left(newlines, match.start()) + 1
                if (kind, line) not in seen:
                    seen.add((kind, line))
                    findings.append((level, f"{path}:{line}: {kind}"))

    for piece in pieces:
        window = carry + piece
        scan(window, False)
        if len(window) > overlap:
            truncated = True
            carry_lines += window.count("\n", 0, len(window) - overlap)
            carry = window[-overlap:]
        else:
            carry = window
    scan(carry, True)
    return findings


def decoded_pieces(stream, codec, head):
    """Decoded text of a byte stream, chunk by chunk; `head` is what was already read from it."""
    decoder = codecs.getincrementaldecoder(codec)("replace")
    data = head
    while data:
        yield decoder.decode(data)
        data = stream.read(CHUNK_BYTES)
    tail = decoder.decode(b"", final=True)
    if tail:
        yield tail


def scan_blob(repo, sha, path, patterns):
    """(findings, note): the findings of one blob streamed from git, or a note when it is binary."""
    command = ["git", "--literal-pathspecs", "-C", repo, "cat-file", "blob", sha]
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          env=git_env()) as process:
        head = process.stdout.read(max(SNIFF_BYTES, CHUNK_BYTES))
        codec = detect_encoding(head[:SNIFF_BYTES])
        if codec is None:
            process.stdout.close()
            process.wait()
            return [], f"{path} is binary; content not scanned"
        findings = scan_windows(decoded_pieces(process.stdout, codec, head), patterns, path)
        process.wait()
        if process.returncode != 0:
            raise RuntimeError(f"git cat-file failed for {path}")
    return findings, None


def scan(repo):
    """(failures, warnings, notes): lists of strings. Raises when the check cannot run."""
    patterns = [(level, kind, re.compile(pattern)) for level, kind, pattern in secret_patterns.SECRET_CONTENT]
    failures, warnings, notes = [], [], []
    for path, sha, mode in staged_blobs(repo):
        if mode == GITLINK:
            continue
        findings, note = scan_blob(repo, sha, path, patterns)
        if note:
            notes.append(note)
        for level, text in findings:
            (failures if level == "FAIL" else warnings).append(text)
    return failures, warnings, notes


def main(argv):
    if len(argv) != 2:
        print("usage: staged_secrets.py <repo>", file=sys.stderr)
        return 2
    try:
        failures, warnings, notes = scan(argv[1])
    except Exception as error:  # fail closed: a check that cannot run never counts as a pass
        print(f"staged_secrets: cannot check staged content: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    for note in notes:
        print(f"staged_secrets: note: {note}", file=sys.stderr)
    for warning in warnings:
        print(f"staged_secrets: warning: {warning}", file=sys.stderr)
    for failure in failures:
        print(failure)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
