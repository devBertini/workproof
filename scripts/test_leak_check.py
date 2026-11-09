"""Tests for leak_check.py. Run: python -B test_leak_check.py

Planted leaks are assembled at run time so this file stays clean for the check itself."""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import leak_check  # noqa: E402

SEP = "\\"
WIN_HOME = "C:" + SEP + "Users" + SEP + "jdoe" + SEP + "src"
MAC_HOME = "/" + "Users/jdoe/src"
LINUX_HOME = "/" + "home/jdoe/src"
GITBASH_HOME = "/c/" + "Users/jdoe/src"
WSL_HOME = "/mnt/c/" + "Users/jdoe"
PRIVATE_MAIL = "jdoe" + "@" + "mail.invalid-provider.net"
NOREPLY_MAIL = "123+jdoe" + "@" + "users.noreply.github.com"


def run_git(root, *args):
    subprocess.run(["git", "-C", root, *args], check=True, capture_output=True)


class ScanText(unittest.TestCase):
    def rules(self, text, patterns=()):
        return [rule for _, rule in leak_check.scan_text(text, list(patterns))]

    def test_home_paths_on_every_system(self):
        for text in (WIN_HOME, MAC_HOME, LINUX_HOME, GITBASH_HOME, WSL_HOME, WIN_HOME.replace(SEP, "/")):
            with self.subTest(text=text):
                self.assertIn("absolute path in a user's home folder", self.rules(text))

    def test_placeholder_users_pass(self):
        for name in ("user", "runner", "<you>", "$USER", "%USERNAME%", "alice"):
            with self.subTest(name=name):
                self.assertEqual(self.rules("/" + "home/" + name + "/project"), [])
                self.assertEqual(self.rules("C:" + SEP + "Users" + SEP + name + SEP + "x"), [])

    def test_placeholder_prefix_does_not_hide_a_real_name(self):
        self.assertTrue(self.rules("/" + "home/username2/x"))

    def test_emails(self):
        self.assertIn("email address", self.rules("contact " + PRIVATE_MAIL))
        self.assertEqual(self.rules("by " + NOREPLY_MAIL + " and dev" + "@" + "example.com"), [])

    def test_private_patterns_case_insensitive(self):
        patterns = [(3, leak_check.re.compile(r"\bzanzibar\b", leak_check.re.IGNORECASE))]
        self.assertEqual(self.rules("visit ZANZIBAR now", patterns),
                         ["private pattern on line 3 of the patterns file"])
        self.assertEqual(self.rules("zanzibars", patterns), [])

    def test_plain_text_passes(self):
        self.assertEqual(self.rules("Run python -B test_x.py in ./scripts; see https://example.com/a@b"), [])


class LoadPatterns(unittest.TestCase):
    def test_comments_blank_and_bad_regex(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "p")
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write("# comment\n\nfoo\r\nbar(\n")
            with self.assertRaises(ValueError) as caught:
                leak_check.load_patterns(path)
            self.assertIn(":4:", str(caught.exception))
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("# comment\n\nfoo\n")
            self.assertEqual([n for n, _ in leak_check.load_patterns(path)], [3])


class Repository(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        run_git(self.root, "init", "-q")
        run_git(self.root, "config", "user.name", "Tester")
        run_git(self.root, "config", "user.email", NOREPLY_MAIL)
        run_git(self.root, "config", "commit.gpgsign", "false")
        self.write(".gitignore", "ignored.txt\n.leak-patterns\n")
        self.write(".leak-patterns", "zanzibar\n")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        path = os.path.join(self.root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)

    def check(self, *extra):
        out = subprocess.run([sys.executable, "-B", leak_check.__file__, "--root", self.root, *extra],
                             capture_output=True, text=True)
        return out.returncode, out.stdout + out.stderr

    def test_clean_repository(self):
        self.write("README.md", "hello\n")
        code, out = self.check("--require-patterns")
        self.assertEqual(code, 0, out)
        self.assertIn("clean", out)

    def test_untracked_file_is_scanned_and_ignored_file_is_not(self):
        self.write("ignored.txt", "zanzibar\n")
        self.write("docs/new.md", "line\nzanzibar\n")
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("docs/new.md:2: private pattern on line 1", out)
        self.assertNotIn("ignored.txt", out)
        self.assertNotIn("zanzibar", out.lower().replace("leak-patterns", ""))

    def test_file_name_is_scanned(self):
        self.write("zanzibar.md", "x\n")
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("file name", out)

    def test_commit_author_and_message(self):
        self.write("a.txt", "x\n")
        run_git(self.root, "add", "a.txt")
        run_git(self.root, "-c", "user.email=" + PRIVATE_MAIL, "commit", "-q", "-m", "add a")
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("email address", out)
        self.assertIn("commit ", out)

    def test_missing_patterns_file(self):
        os.remove(os.path.join(self.root, ".leak-patterns"))
        self.write("a.txt", "x\n")
        self.assertEqual(self.check()[0], 0)
        self.assertEqual(self.check("--require-patterns")[0], 2)

    def test_binary_file_skipped(self):
        path = os.path.join(self.root, "blob.bin")
        with open(path, "wb") as handle:
            handle.write(b"\0zanzibar")
        self.assertEqual(self.check()[0], 0)

    def test_not_a_repository(self):
        with tempfile.TemporaryDirectory() as other:
            code, _ = subprocess.run([sys.executable, "-B", leak_check.__file__, "--root", other],
                                     capture_output=True, text=True).returncode, None
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
