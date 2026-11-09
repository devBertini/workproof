"""Tests of guarded-commit (bin/guarded-commit through a POSIX shell) and staged_secrets.py in throwaway repositories.

Run in this folder: python -B test_guarded_commit.py
Every fake secret is built at run time from fragments, so no literal secret sits in a source file.
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import guarded_commit  # noqa: E402
import secret_patterns  # noqa: E402
import staged_secrets  # noqa: E402
from test_support import (fake_aws_key, fake_github_token, fake_google_key, fake_pem, find_sh,  # noqa: E402
                          posix)

BIN = os.path.join(os.path.dirname(HERE), "bin", "guarded-commit")
HELPER = os.path.join(HERE, "staged_secrets.py")
SH = find_sh()
EXTRA_ENV = secret_patterns.EXTRA_NAMES_ENV

# label -> (builder of a file's text, the kind reported)
SECRETS = {
    "private key": (lambda: "key = '''\n" + fake_pem() + "'''\n", "private key block"),
    "key with an id line": (lambda: "key id: ABC" + "123DEFG\n" + fake_pem(), "private key block"),
    "GitHub token": (lambda: "token=" + fake_github_token() + "\n", "GitHub token"),
    "AWS key": (lambda: "aws_access_key_id = " + fake_aws_key() + "\n", "AWS access key ID"),
}


def guarded(*args, env=None):
    if not SH:
        raise unittest.SkipTest("no POSIX shell")
    full_env = {k: v for k, v in os.environ.items() if k != EXTRA_ENV}
    full_env.update(env or {})
    return subprocess.run([SH, posix(BIN), *args], capture_output=True, text=True, env=full_env, timeout=60)


def helper(repo):
    return subprocess.run([sys.executable, "-B", HELPER, repo], capture_output=True, text=True)


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True).stdout


class Repo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="gc ")  # a space in the path on purpose
        self.d = self.tmp.name
        for c in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "t"],
                  ["config", "commit.gpgsign", "false"]):
            git(self.d, *c)
        self.w("seed.txt", "x")
        git(self.d, "add", ".")
        git(self.d, "commit", "-q", "-m", "seed")

    def tearDown(self):
        self.tmp.cleanup()

    def w(self, name, text="y"):
        path = os.path.join(self.d, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", newline="") as handle:
            handle.write(text)

    def wb(self, name, data):
        with open(os.path.join(self.d, name), "wb") as handle:
            handle.write(data)

    def staged(self):
        return git(self.d, "diff", "--cached", "--name-only")

    def count(self):
        return int(git(self.d, "rev-list", "--count", "HEAD"))


class Commits(Repo):
    def test_commits_only_the_given_paths(self):
        self.w("a.txt")
        self.w("b.txt")
        r = guarded(self.d, "add a", "a.txt")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.count(), 2)
        status = git(self.d, "status", "--porcelain")
        self.assertIn("b.txt", status)
        self.assertNotIn("a.txt", status)
        self.assertIn("1 file changed", r.stdout)

    def test_any_message_style_goes_through_untouched(self):
        self.w("a.txt")
        message = "Added a thing, with \"quotes\" and $dollars\n\nand a body"
        r = guarded(self.d, message, "a.txt")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(git(self.d, "log", "-1", "--format=%B").strip(), message)

    def test_paths_with_spaces_and_folders(self):
        self.w("my notes/a b.txt")
        r = guarded(self.d, "notes", "my notes/a b.txt")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("my notes/a b.txt", git(self.d, "show", "--name-only", "--format=", "HEAD"))

    def test_a_folder_path_takes_what_is_inside(self):
        self.w("src/one.txt")
        self.w("src/two.txt")
        self.w("other.txt")
        self.assertEqual(guarded(self.d, "src", "src").returncode, 0)
        self.assertEqual(sorted(git(self.d, "show", "--name-only", "--format=", "HEAD").split()),
                         ["src/one.txt", "src/two.txt"])

    def test_deletions_work(self):
        os.remove(os.path.join(self.d, "seed.txt"))
        self.assertEqual(guarded(self.d, "drop seed", "seed.txt").returncode, 0)
        self.assertEqual(self.count(), 2)

    def test_the_first_commit_of_an_empty_repository(self):
        with tempfile.TemporaryDirectory() as fresh:
            for c in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "t"],
                      ["config", "commit.gpgsign", "false"]):
                git(fresh, *c)
            with open(os.path.join(fresh, "a.txt"), "w") as handle:
                handle.write("a")
            self.assertEqual(guarded(fresh, "first", "a.txt").returncode, 0)
            with open(os.path.join(fresh, ".env"), "w") as handle:
                handle.write("A=1")
            self.assertEqual(guarded(fresh, "second", ".env").returncode, 1)
            self.assertEqual(git(fresh, "diff", "--cached", "--name-only"), "")

    def test_nothing_to_commit(self):
        r = guarded(self.d, "nothing", "seed.txt")
        self.assertEqual(r.returncode, 1)
        self.assertIn("nothing to commit", r.stderr)

    def test_clean_files_that_only_look_like_secrets_commit(self):
        self.w("a.swift", "let sk = 'sk-short'\nlet key = \"AKIA is a prefix\"\n-----BEGIN CERTIFICATE-----\n")
        r = guarded(self.d, "a", "a.swift")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_secret_only_in_an_unstaged_file_does_not_block(self):
        self.w("a.txt", "fine\n")
        self.w("other.txt", fake_github_token())
        self.assertEqual(guarded(self.d, "a", "a.txt").returncode, 0)

    def test_a_placeholder_env_example_commits_but_its_content_is_still_scanned(self):
        self.w("a.txt")
        self.w("deploy/.env.example", "PORT=8390\n")
        r = guarded(self.d, "x", "a.txt", "deploy/.env.example")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.w(".env.example", "KEY=" + fake_aws_key() + "\n")
        r = guarded(self.d, "y", ".env.example")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.count(), 2)

    def test_deleting_a_file_that_once_held_a_secret_works(self):
        self.w("old.txt", fake_github_token())
        git(self.d, "add", "old.txt")
        git(self.d, "commit", "-q", "-m", "old")
        os.remove(os.path.join(self.d, "old.txt"))
        self.assertEqual(guarded(self.d, "drop old", "old.txt").returncode, 0)
        self.assertEqual(self.count(), 3)

    def test_deleting_a_secret_named_file_works(self):
        self.w("keys/old.pem", "not a key")
        git(self.d, "add", "-f", "keys/old.pem")
        git(self.d, "commit", "-q", "-m", "old")
        os.remove(os.path.join(self.d, "keys", "old.pem"))
        self.assertEqual(guarded(self.d, "drop key", "keys/old.pem").returncode, 0)


class RefusesNames(Repo):
    def test_refuses_secret_file_names_and_stages_nothing(self):
        self.w("a.txt")
        for bad in ("keys/AuthKey.p8", ".env.local", "id_rsa", "deploy/server.pem", "cfg/.aws/credentials",
                    "app/release.keystore"):
            with self.subTest(bad=bad):
                self.w(bad)
                r = guarded(self.d, "x", "a.txt", bad)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn(bad, r.stderr)
                self.assertEqual(self.count(), 1)
                self.assertEqual(self.staged(), "")

    def test_env_example_siblings_are_not_placeholders(self):
        for bad in (".env.examples", ".env.example.local"):
            self.w(bad, "x")
            self.assertEqual(guarded(self.d, "z", bad).returncode, 1, bad)
        self.assertEqual(self.count(), 1)

    def test_extra_names_from_the_option_apply_when_the_environment_carries_them(self):
        self.w("prod.vault", "x")
        self.assertEqual(guarded(self.d, "v", "prod.vault", env={EXTRA_ENV: "*.vault"}).returncode, 1)
        self.assertEqual(self.staged(), "")
        self.assertEqual(guarded(self.d, "v", "prod.vault").returncode, 0)


class RefusesContent(Repo):
    def test_staged_content_with_a_secret_is_refused_and_never_echoed(self):
        for label, (make, kind) in SECRETS.items():
            with self.subTest(label):
                text = make()
                self.w("clean.txt", "fine\n")
                self.w("src/notes.txt", "line one\nline two\n" + text)
                r = guarded(self.d, "add notes", "clean.txt", "src/notes.txt")
                out = r.stdout + r.stderr
                self.assertEqual(r.returncode, 1, label)
                self.assertEqual(self.count(), 1, label)
                self.assertEqual(self.staged(), "", label)
                self.assertIn("src/notes.txt", r.stderr, label)
                self.assertIn(kind, r.stderr, label)
                self.assertNotIn("clean.txt:", r.stderr, label)
                for line in text.splitlines():
                    if len(line) >= 16:
                        self.assertNotIn(line.strip(), out, label)
                for value in (fake_github_token(), fake_aws_key(), "ABC123DEFG"):
                    self.assertNotIn(value, out, label)
                os.remove(os.path.join(self.d, "src", "notes.txt"))
                os.remove(os.path.join(self.d, "clean.txt"))

    def test_the_line_of_the_hit_is_named(self):
        self.w("a.txt", "one\ntwo\n" + fake_aws_key() + "\n")
        r = guarded(self.d, "a", "a.txt")
        self.assertEqual(r.returncode, 1)
        self.assertIn("a.txt:3: AWS access key ID", r.stderr)

    def test_a_secret_in_a_later_file_is_found_among_clean_ones(self):
        for n in ("a.txt", "b.txt", "c.txt"):
            self.w(n, "fine\n")
        self.w("d file.txt", fake_github_token())
        r = guarded(self.d, "many", "a.txt", "b.txt", "c.txt", "d file.txt")
        self.assertEqual(r.returncode, 1)
        self.assertIn("d file.txt:1: GitHub token", r.stderr)
        self.assertEqual(self.staged(), "")

    def test_a_google_api_key_warns_but_does_not_block(self):
        self.w("a.plist", fake_google_key() + "\n")
        r = guarded(self.d, "a", "a.plist")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("warning: a.plist:1: Google API key", r.stderr)
        self.assertNotIn(fake_google_key(), r.stdout + r.stderr)

    def test_binary_files_and_files_over_2_mb_are_skipped_with_a_note(self):
        self.wb("blob.bin", b"\0\1" + fake_github_token().encode())
        self.wb("big.txt", fake_github_token().encode() + b"\n" + b"x" * (2 * 1024 * 1024 + 10))
        r = guarded(self.d, "blobs", "blob.bin", "big.txt")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("blob.bin is binary", r.stderr)
        self.assertIn("content not scanned", r.stderr)
        self.assertEqual(self.count(), 2)

    def test_a_check_that_cannot_run_refuses_the_commit(self):
        self.w("a.txt", "fine\n")
        original = staged_secrets.scan

        def broken(repo):
            raise RuntimeError("boom")

        staged_secrets.scan = broken
        try:
            with self.assertRaises(guarded_commit.Refused) as caught:
                guarded_commit.commit(self.d, "a", ["a.txt"])
        finally:
            staged_secrets.scan = original
        self.assertIn("could not be checked", str(caught.exception))
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.staged(), "")


class Usage(Repo):
    def test_something_already_staged_is_refused(self):
        self.w("a.txt")
        self.w("b.txt")
        git(self.d, "add", "b.txt")
        r = guarded(self.d, "a", "a.txt")
        self.assertEqual(r.returncode, 1)
        self.assertIn("already staged", r.stderr)
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.staged().strip(), "b.txt")

    def test_not_a_repository_and_bad_usage(self):
        with tempfile.TemporaryDirectory() as plain:
            r = guarded(plain, "m", "a.txt")
            self.assertEqual(r.returncode, 1)
            self.assertIn("not a git repository", r.stderr)
        self.assertEqual(guarded(self.d, "only a message").returncode, 1)
        self.assertEqual(guarded().returncode, 1)
        self.w("a.txt")
        self.assertEqual(guarded(self.d, "  ", "a.txt").returncode, 1)

    def test_a_path_outside_the_repository_is_refused(self):
        r = guarded(self.d, "m", "../elsewhere.txt")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.count(), 1)


class StagedBlob(Repo):
    def test_the_check_reads_the_staged_blob_not_the_working_copy(self):
        self.w("a.txt", fake_github_token())
        git(self.d, "add", "a.txt")
        self.w("a.txt", "clean now\n")
        r = helper(self.d)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("a.txt:1: GitHub token", r.stdout)
        git(self.d, "reset", "-q")
        self.w("a.txt", "clean\n")
        git(self.d, "add", "a.txt")
        self.w("a.txt", fake_github_token())
        self.assertEqual(helper(self.d).returncode, 0)

    def test_a_file_just_under_the_size_limit_is_scanned(self):
        self.w("edge.txt", fake_github_token() + "\n" + "x" * (2 * 1024 * 1024 - 100))
        git(self.d, "add", "edge.txt")
        self.assertEqual(helper(self.d).returncode, 1)

    def test_the_helper_needs_one_argument_and_fails_closed(self):
        r = subprocess.run([sys.executable, "-B", HELPER], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        with tempfile.TemporaryDirectory() as plain:
            self.assertEqual(helper(plain).returncode, 2)


class Wrapper(unittest.TestCase):
    def test_the_wrapper_is_a_posix_sh_script_that_probes_for_python(self):
        with open(BIN, "rb") as handle:
            data = handle.read()
        self.assertTrue(data.startswith(b"#!/bin/sh\n"))
        self.assertNotIn(b"\r\n", data)
        for needle in (b"python3", b"python", b"py -3", b"-c ''", b"guarded_commit.py"):
            self.assertIn(needle, data)

    def test_without_a_working_python_it_says_so(self):
        if not SH or os.name == "nt":
            self.skipTest("needs a POSIX system where sh and git can run without python on PATH")
        with tempfile.TemporaryDirectory() as empty:
            env = {"PATH": empty}
            r = subprocess.run(["/bin/sh", BIN], capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 127)
            self.assertIn("no working Python 3", r.stderr)


if __name__ == "__main__":
    unittest.main()
