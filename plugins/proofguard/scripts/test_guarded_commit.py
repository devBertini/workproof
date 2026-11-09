"""Tests of guarded-commit (bin/guarded-commit through a POSIX shell) and staged_secrets.py in throwaway repositories.

Run in this folder: python -B test_guarded_commit.py
Every fake secret is built at run time from fragments, so no literal secret sits in a source file.
"""
import codecs
import io
import os
import re
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
PLAIN_ENV = secret_patterns.EXTRA_NAMES_PLAIN_ENV

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
    full_env = {k: v for k, v in os.environ.items() if k not in (EXTRA_ENV, PLAIN_ENV)}
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

    def test_extra_names_from_the_plain_variable_apply_too(self):
        self.w("prod.vault", "x")
        self.assertEqual(guarded(self.d, "v", "prod.vault", env={PLAIN_ENV: "*.vault"}).returncode, 1)
        self.assertEqual(guarded(self.d, "v", "prod.vault", env={EXTRA_ENV: "*.other", PLAIN_ENV: "*.vault"}).returncode, 1)
        self.assertEqual(self.staged(), "")


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

    def test_real_binary_files_are_skipped_with_a_note(self):
        self.wb("blob.bin", b"\0\1" + fake_github_token().encode())
        self.wb("noise.dat", bytes(range(256)) * 64 + fake_github_token().encode())
        r = guarded(self.d, "blobs", "blob.bin", "noise.dat")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("blob.bin is binary", r.stderr)
        self.assertIn("noise.dat is binary", r.stderr)
        self.assertIn("content not scanned", r.stderr)
        self.assertEqual(self.count(), 2)

    def test_a_large_text_file_is_scanned_wherever_the_secret_sits(self):
        filler = b"lorem ipsum dolor sit amet\n" * (3 * 1024 * 1024 // 27 + 1)  # about 3 MB
        for label, data, line in (
                ("start", fake_github_token().encode() + b"\n" + filler, 1),
                ("end", filler + fake_github_token().encode(), filler.count(b"\n") + 1)):
            with self.subTest(label):
                self.wb("big.txt", data)
                r = guarded(self.d, "big", "big.txt")
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn(f"big.txt:{line}: GitHub token", r.stderr)
                self.assertEqual(self.count(), 1)
                self.assertEqual(self.staged(), "")

    def test_a_large_clean_text_file_commits_without_a_skip_note(self):
        self.wb("big.txt", b"lorem ipsum dolor sit amet\n" * (3 * 1024 * 1024 // 27 + 1))
        r = guarded(self.d, "big", "big.txt")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("not scanned", r.stderr)

    def test_utf16_and_utf32_text_is_decoded_and_scanned(self):
        text = "line one\r\nline two\r\ntoken=" + fake_github_token() + "\r\n"
        variants = {
            "UTF-16 LE with BOM": codecs.BOM_UTF16_LE + text.encode("utf-16-le"),
            "UTF-16 BE with BOM": codecs.BOM_UTF16_BE + text.encode("utf-16-be"),
            "UTF-16 LE without BOM": text.encode("utf-16-le"),
            "UTF-16 BE without BOM": text.encode("utf-16-be"),
            "UTF-32 LE with BOM": codecs.BOM_UTF32_LE + text.encode("utf-32-le"),
            "UTF-32 BE with BOM": codecs.BOM_UTF32_BE + text.encode("utf-32-be"),
        }
        for label, data in variants.items():
            with self.subTest(label):
                self.wb("ps.txt", data)
                r = guarded(self.d, "ps", "ps.txt")
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn("ps.txt:3: GitHub token", r.stderr)
                self.assertNotIn("binary", r.stderr)
                self.assertEqual(self.count(), 1)
                self.assertEqual(self.staged(), "")

    def test_clean_utf16_text_commits_and_is_not_called_binary(self):
        self.wb("ps.txt", codecs.BOM_UTF16_LE + "nothing secret here\r\n".encode("utf-16-le"))
        self.wb("nobom.txt", "nothing secret here either\r\n".encode("utf-16-le"))
        r = guarded(self.d, "ps", "ps.txt", "nobom.txt")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("binary", r.stderr)

    def test_a_large_utf16_file_is_scanned(self):
        text = "lorem ipsum dolor sit amet\r\n" * 120000 + "key=" + fake_aws_key() + "\r\n"
        self.wb("big16.txt", text.encode("utf-16-le"))
        r = guarded(self.d, "big", "big16.txt")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("big16.txt:120001: AWS access key ID", r.stderr)

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


class LiteralPaths(Repo):
    """Every path argument is a file name: no glob, bracket class or pathspec magic reaches git."""

    def test_brackets_commit_only_that_exact_file(self):
        self.w("file[1].txt")
        self.w("file1.txt")
        r = guarded(self.d, "brackets", "file[1].txt")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(git(self.d, "show", "--name-only", "--format=", "HEAD").split(), ["file[1].txt"])
        self.assertIn("file1.txt", git(self.d, "status", "--porcelain"))

    def test_a_bracket_name_that_does_not_exist_does_not_fall_back_to_a_glob(self):
        self.w("file1.txt")
        r = guarded(self.d, "brackets", "file[1].txt")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.staged(), "")

    def test_a_star_is_not_a_wildcard(self):
        self.w("a.txt")
        self.w("b.txt")
        r = guarded(self.d, "star", "*.txt")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.staged(), "")
        if os.name != "nt":  # a file may be named with a star only where the file system allows it
            self.w("s*r.txt")
            self.assertEqual(guarded(self.d, "star", "s*r.txt").returncode, 0)
            self.assertEqual(git(self.d, "show", "--name-only", "--format=", "HEAD").split(), ["s*r.txt"])

    def test_pathspec_magic_with_a_leading_colon_matches_nothing(self):
        self.w("a.txt")
        self.w("sub/b.txt")
        for spec in (":(glob)*.txt", ":(glob)**/*.txt", ":/sub/b.txt", ":/", ":!seed.txt", ":(top)a.txt",
                     ":(icase)A.TXT", ":^a.txt"):
            with self.subTest(spec=spec):
                with self.assertRaises(guarded_commit.Refused):
                    guarded_commit.commit(self.d, "magic", [spec])
                self.assertEqual(self.count(), 1)
                self.assertEqual(self.staged(), "")

    def test_a_leading_colon_through_the_command_line(self):
        self.w("a.txt")
        r = guarded(self.d, "magic", ":(glob)*.txt")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.staged(), "")

    def test_a_file_named_with_a_leading_colon_is_taken_literally(self):
        if os.name == "nt":
            self.skipTest("Windows file names cannot hold a colon")
        self.w(":(glob)x.txt")
        self.w("y.txt")
        self.assertEqual(guarded(self.d, "colon", ":(glob)x.txt").returncode, 0)
        self.assertEqual(git(self.d, "show", "--name-only", "--format=", "HEAD").split(), [":(glob)x.txt"])

    def test_pathspec_settings_in_the_environment_do_not_matter(self):
        self.w("file[1].txt")
        self.w("file1.txt")
        r = guarded(self.d, "brackets", "file[1].txt", env={"GIT_GLOB_PATHSPECS": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(git(self.d, "show", "--name-only", "--format=", "HEAD").split(), ["file[1].txt"])


class ScanWindows(unittest.TestCase):
    """The scan over decoded text pieces, with chunk and overlap sizes small enough to cross every boundary."""

    PATTERNS = [(level, kind, re.compile(pattern)) for level, kind, pattern in secret_patterns.SECRET_CONTENT]
    OVERLAP = 300  # above the longest bounded match (a fine-grained GitHub token is 266 characters)

    def findings(self, text, piece):
        pieces = [text[i:i + piece] for i in range(0, len(text), piece)]
        return [found for _, found in staged_secrets.scan_windows(pieces, self.PATTERNS, "p", self.OVERLAP)]

    def test_a_secret_is_found_once_at_every_offset_across_chunk_boundaries(self):
        tokens = {"GitHub token": fake_github_token(), "AWS access key ID": fake_aws_key(),
                  "GitHub token (fine-grained, longest)": "github" + "_pat_" + "aB3dE5gH7j" * 25 + "aB3dE",
                  "private key block": "-----BEGIN RSA " + "PRIVATE KEY-----"}
        for label, token in tokens.items():
            for offset in range(0, 520, 7):
                prefix = ("abc def\n" * 100)[:offset] + " "  # a word boundary before the token
                text = prefix + token + "\nafter " + "z" * 400 + "\n"
                line = prefix.count("\n") + 1
                found = self.findings(text, piece=100)
                self.assertEqual(len(found), 1, (label, offset, found))
                self.assertTrue(found[0].startswith(f"p:{line}: "), (label, offset, found))

    def test_a_secret_ending_exactly_at_the_end_of_the_text_is_found(self):
        for offset in range(0, 450, 13):
            self.assertEqual(len(self.findings("q " * (offset // 2) + fake_aws_key(), piece=100)), 1, offset)
            self.assertEqual(len(self.findings(fake_github_token() + " " * offset, piece=100)), 1, offset)

    def test_a_secret_in_a_text_shorter_than_the_overlap_is_found(self):
        self.assertEqual(self.findings(fake_github_token(), piece=1000), ["p:1: GitHub token"])
        self.assertEqual(self.findings(fake_aws_key() + "\n", piece=1000), ["p:1: AWS access key ID"])

    def test_a_key_longer_than_the_overlap_is_found_once_on_its_line(self):
        for offset in range(0, 450, 17):
            prefix = "n\n" * (offset // 2)
            found = self.findings(prefix + "sk" + "-" + "a" * 3000 + "\ntail\n", piece=100)
            self.assertEqual(found, [f"p:{offset // 2 + 1}: OpenAI API key"], offset)

    def test_a_lookalike_cut_by_a_boundary_is_not_a_secret(self):
        for offset in range(0, 450, 3):
            prefix = "." * offset
            self.assertEqual(self.findings(prefix + "x" + fake_aws_key(), piece=100), [], offset)
            self.assertEqual(self.findings(prefix + fake_aws_key() + "Z9", piece=100), [], offset)
            self.assertEqual(self.findings(prefix + fake_aws_key() + "\n", piece=100), ["p:1: AWS access key ID"], offset)

    def test_line_numbers_count_across_many_windows(self):
        text = "".join(f"row {i}\n" for i in range(2000)) + fake_github_token() + "\n"
        self.assertEqual(self.findings(text, piece=64), ["p:2001: GitHub token"])

    def test_utf16_split_inside_a_character_decodes_whole(self):
        data = codecs.BOM_UTF16_LE + ("token=" + fake_github_token() + "\n").encode("utf-16-le")
        for size in (1, 2, 3, 5, 7):
            with self.subTest(size=size):
                original = staged_secrets.CHUNK_BYTES
                staged_secrets.CHUNK_BYTES = size
                try:
                    stream = io.BytesIO(data[size:])
                    text = "".join(staged_secrets.decoded_pieces(stream, "utf-16", data[:size]))
                finally:
                    staged_secrets.CHUNK_BYTES = original
                self.assertEqual(text, "token=" + fake_github_token() + "\n")

    def test_encoding_detection(self):
        detect = staged_secrets.detect_encoding
        self.assertEqual(detect(b"plain text"), "utf-8")
        self.assertEqual(detect(b""), "utf-8")
        self.assertEqual(detect(codecs.BOM_UTF16_LE + b"a\0"), "utf-16")
        self.assertEqual(detect(codecs.BOM_UTF16_BE + b"\0a"), "utf-16")
        self.assertEqual(detect(codecs.BOM_UTF32_LE + b"a\0\0\0"), "utf-32")
        self.assertEqual(detect("hello world, plain".encode("utf-16-le")), "utf-16-le")
        self.assertEqual(detect("hello world, plain".encode("utf-16-be")), "utf-16-be")
        self.assertIsNone(detect(b"\0\1\2\3\4\5"))
        self.assertIsNone(detect(bytes(range(256)) * 4))
        self.assertIsNone(detect(b"\0" * 100))


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

    def test_a_file_of_any_size_is_scanned(self):
        for size in (2 * 1024 * 1024 - 100, 2 * 1024 * 1024 + 100, 5 * 1024 * 1024):
            with self.subTest(size=size):
                self.w("edge.txt", fake_github_token() + "\n" + "x" * size)
                git(self.d, "add", "edge.txt")
                self.assertEqual(helper(self.d).returncode, 1)
                git(self.d, "reset", "-q")

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
