"""Tests of the optional commit rules (commit_rules.py) as guarded-commit applies them, in throwaway repositories.

Run in this folder: python -B test_commit_rules.py
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_support import fake_github_token  # noqa: E402

SCRIPT = os.path.join(HERE, "guarded_commit.py")
CLEAN = ("WORKPROOF_", "CLAUDE_PLUGIN_OPTION_")
CONVENTIONAL = {"WORKPROOF_COMMIT_CONVENTIONAL": "on"}


def env_with(extra):
    full = {k: v for k, v in os.environ.items() if not k.startswith(CLEAN)}
    full.update(extra or {})
    return full


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True).stdout


class Repo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cr ")
        self.d = self.tmp.name
        for c in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "t"],
                  ["config", "commit.gpgsign", "false"]):
            git(self.d, *c)
        self.w("seed.txt", "x")
        git(self.d, "add", ".")
        git(self.d, "commit", "-q", "-m", "chore: seed")

    def tearDown(self):
        self.tmp.cleanup()

    def w(self, name, text="y", size=None):
        path = os.path.join(self.d, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"\0" * size if size else text.encode())

    def gc(self, message, *paths, env=None):
        return subprocess.run([sys.executable, "-B", SCRIPT, self.d, message, *paths], capture_output=True,
                              text=True, env=env_with(env), timeout=60)

    def staged(self):
        return git(self.d, "diff", "--cached", "--name-only")

    def count(self):
        return int(git(self.d, "rev-list", "--count", "HEAD"))

    def assertRefused(self, result, text=None):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        if text:
            self.assertIn(text, result.stderr)
        self.assertEqual((self.count(), self.staged()), (1, ""))

    def test_rules_are_off_by_default(self):
        self.w("big.bin", size=6_000_000); self.w("docs/a.png")
        r = self.gc("free text message", "big.bin", "docs/a.png")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_conventional_message(self):
        self.w("a.txt")
        for bad in ("added a thing", "feat: a\n\nbody", "feat:a", "wip: a", "feat(Bad Scope): a"):
            self.assertRefused(self.gc(bad, "a.txt", env=CONVENTIONAL), "Conventional" if "\n" not in bad else "one line")
        for good in ("fix(core)!: a", "chore(repo/sub): b"):
            self.w("a.txt", good)
            r = self.gc(good, "a.txt", env=CONVENTIONAL)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_custom_types_turn_the_rule_on(self):
        types = {"WORKPROOF_COMMIT_TYPES": "add, change"}
        self.w("a.txt")
        self.assertRefused(self.gc("feat: a", "a.txt", env=types))
        self.assertEqual(self.gc("change: a", "a.txt", env=types).returncode, 0)

    def test_size_cap(self):
        self.w("big.bin", size=1_200_000)
        self.assertRefused(self.gc("feat: big", "big.bin", env={"WORKPROOF_COMMIT_MAX_MB": "1"}), "over 1 MB")
        self.assertRefused(self.gc("feat: big", "big.bin", env={"WORKPROOF_COMMIT_MAX_MB": "x"}), "not a number")
        self.assertEqual(self.gc("feat: big", "big.bin", env={"WORKPROOF_COMMIT_MAX_MB": "0"}).returncode, 0)

    def test_blocked_new_files_with_exceptions(self):
        rules = {"WORKPROOF_COMMIT_BLOCK_NEW": "docs/**/*.png, *.wav, !docs/reference/**"}
        self.w("docs/shots/a.png"); self.w("sound.wav"); self.w("docs/reference/sketch.png")
        self.assertRefused(self.gc("docs: shot", "docs/shots/a.png", env=rules), "docs/shots/a.png")
        self.assertRefused(self.gc("docs: sound", "sound.wav", env=rules), "sound.wav")
        self.assertEqual(self.gc("docs: reference", "docs/reference/sketch.png", env=rules).returncode, 0)

    def test_blocked_rule_applies_only_to_new_files(self):
        self.w("docs/old.png", "1")
        self.assertEqual(self.gc("docs: old", "docs/old.png").returncode, 0)
        self.w("docs/old.png", "2")
        r = self.gc("docs: update", "docs/old.png", env={"WORKPROOF_COMMIT_BLOCK_NEW": "docs/*.png"})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_secrets_still_refused_with_rules_on(self):
        self.w("a.txt", "one\n" + fake_github_token() + "\n")
        r = self.gc("feat: a", "a.txt", env=dict(CONVENTIONAL, WORKPROOF_COMMIT_MAX_MB="5"))
        self.assertRefused(r, "a.txt:2: GitHub token")
        self.assertNotIn(fake_github_token(), r.stdout + r.stderr)

    def test_deletion_passes_the_path_rules(self):
        os.remove(os.path.join(self.d, "seed.txt"))
        r = self.gc("chore: drop seed", "seed.txt", env={"WORKPROOF_COMMIT_MAX_MB": "1",
                                                          "WORKPROOF_COMMIT_BLOCK_NEW": "*.txt"})
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
