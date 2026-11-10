"""The skills-only edition (plugins/workproof-skills) matches what tools/build_skills_edition.py builds from workproof."""
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    tool = os.path.join(REPO, "tools", "build_skills_edition.py")
    result = subprocess.run([sys.executable, "-B", tool, "--check"], capture_output=True, text=True)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
