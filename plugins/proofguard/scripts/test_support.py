"""Helpers shared by the test files: finding a POSIX shell and building fake secrets at run time."""
import os
import shutil

WINDOWS = os.name == "nt"


def find_sh():
    """A POSIX shell for the tests; on Windows Git Bash, never the WSL launcher in System32. None when absent."""
    if WINDOWS:
        for path in (os.environ.get("CLAUDE_CODE_GIT_BASH_PATH"), shutil.which("bash"),
                     r"C:\Program Files\Git\bin\bash.exe"):
            if path and os.path.isfile(path) and "system32" not in path.lower():
                return path
        return None
    return shutil.which("sh")


def posix(path):
    """The path with forward slashes, which Git Bash accepts for a Windows drive path."""
    return str(path).replace("\\", "/")


# Fake secrets: assembled from fragments so that no literal secret sits in a source file.
def fake_github_token():
    return "gh" + "p_" + "aB3dE5gH7jK9" * 3


def fake_aws_key():
    return "AK" + "IA" + "QWERTYUIOP123456"


def fake_google_key():
    return "AI" + "za" + "SyA" + "x" * 32


def fake_pem(kind="PRIVATE"):
    body = "\n".join(["MIGT" + "AgEAMBMGByqGSM49AgEG" * 2, "CCqGSM49AwEHBHkw" * 3])
    return f"-----BEGIN {kind} KEY-----\n{body}\n-----END {kind} KEY-----\n"
