#!/usr/bin/env python
"""The gate on Linux, in two containers side by side: the minimum Python (3.9) and the newest.

    python -B tools/check_linux.py [--keep-images]

Each container clones the repository (read-only mount) the way a user's machine receives it, so only committed
files with the modes the git index records are there, then runs `scripts/check_all.py` and one hook round trip
through the sh half of the launcher. That proves, without a push: the executable bits, the minimum Python, the POSIX
path of the launcher and the bin/ tools, and that nothing leans on an untracked file. `claude` is not in the
containers, so the plugin's own tests and validation are not part of this run (the gate prints a note).

The images are pinned by digest and removed afterwards unless --keep-images. Needs a running Docker engine.
Exit 0 when both containers passed. Python 3.9+, standard library only.
"""
import concurrent.futures
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGES = {
    "python 3.9 (minimum)": "python:3.9-slim@sha256:2d97f6910b16bd338d3060f261f53f144965f755599aab1acda1e13cf1731b1b",
    "python 3.14 (newest)": "python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f",
}
# Run inside the container. The hook round trip: an allowed command exits 0, a read of a secret file exits 2.
INNER = r"""
set -e
apt-get update -qq >/dev/null && apt-get install -y -qq --no-install-recommends git >/dev/null
git config --global --add safe.directory '*'
git config --global user.name ci && git config --global user.email ci@example.invalid
git config --global init.defaultBranch main
git clone -q /src /work
cd /work
python --version
python -B scripts/check_all.py
hook="/work/plugins/workproof/hooks/run-hook.cmd"
test -x "$hook"
printf '%s' '{"tool_name":"Bash","tool_input":{"command":"ls -la"}}' | sh -c "\"$hook\" secret_guard.py"
set +e
printf '%s' '{"tool_name":"Bash","tool_input":{"command":"cat id_rsa"}}' | sh -c "\"$hook\" secret_guard.py" 2>/dev/null
code=$?
printf '%s' '{"tool_name":"Read","tool_input":{"file_path":"/w/id_rsa"}}' | sh -c "\"$hook\" file_guard.py" 2>/dev/null
file_code=$?
set -e
test "$code" = 2 && test "$file_code" = 2
/work/plugins/workproof/bin/workproof-doctor
/work/plugins/workproof/bin/work-plan show --where /work >/dev/null 2>&1 || true
echo "LINUX OK"
"""


def run(item):
    label, image = item
    mount = REPO.replace("\\", "/") + ":/src:ro"
    out = subprocess.run(["docker", "run", "--rm", "-v", mount, image, "sh", "-c", INNER],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3000,
                         env=dict(os.environ, MSYS_NO_PATHCONV="1"))
    return label, image, out.returncode, (out.stdout or "") + (out.stderr or "")


def main(argv):
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(IMAGES)) as pool:
        results = list(pool.map(run, IMAGES.items()))
    failed = 0
    for label, image, code, text in results:
        passed = code == 0 and "LINUX OK" in text
        failed += not passed
        print(f"{'ok  ' if passed else 'FAIL'}  {label}: exit {code}")
        for line in text.strip().splitlines()[-(8 if passed else 45):]:
            print(f"        {line}")
    if "--keep-images" not in argv:
        for image in IMAGES.values():
            subprocess.run(["docker", "image", "rm", image], capture_output=True)
    print("check_linux: " + ("both containers passed" if not failed else f"{failed} container(s) failed"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
