import os
import subprocess
import tempfile
import unittest
from unittest import mock

from review.gitutil import GitError, diff_text, git, is_ancestor, read_blobs


def run(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                          text=True).stdout.strip()


class GitUtilTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.repo = cls._tmp.name
        run(cls.repo, "init", "-q")
        run(cls.repo, "config", "user.email", "t@example.com")
        run(cls.repo, "config", "user.name", "t")
        cls.c1 = cls._commit("a.txt", "first\n")
        cls.c2 = cls._commit("a.txt", "first\nsecond\n")
        cls.c3 = cls._commit("한글.txt", "x\n")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @classmethod
    def _commit(cls, name, content):
        with open(os.path.join(cls.repo, name), "w", encoding="utf-8") as f:
            f.write(content)
        run(cls.repo, "add", name)
        run(cls.repo, "commit", "-q", "-m", "m")
        return run(cls.repo, "rev-parse", "HEAD")

    def test_read_blobs(self):
        c = self._commit("bin.txt", "ok\n")
        with open(os.path.join(self.repo, "bad.txt"), "wb") as f:
            f.write(b"a\xffb\nlast")
        run(self.repo, "add", "bad.txt")
        run(self.repo, "commit", "-q", "-m", "bad")
        got = read_blobs(self.repo, "HEAD", ["a.txt", "nope.txt", "bad.txt", "한글.txt", "x\ny"])
        self.assertEqual(got, {"a.txt": ["first", "second"], "bad.txt": ["a\ufffdb", "last"],
                               "한글.txt": ["x"]})
        self.assertEqual(read_blobs(self.repo, c, []), {})

    def test_diff_text_two_and_three_dot(self):
        self.assertIn("+second", diff_text(self.repo, self.c1, self.c2, merge_base=True))
        self.assertIn("+second", diff_text(self.repo, self.c1, self.c2, merge_base=False))

    def test_is_ancestor(self):
        self.assertTrue(is_ancestor(self.repo, self.c1, self.c2))
        self.assertFalse(is_ancestor(self.repo, self.c2, self.c1))
        self.assertFalse(is_ancestor(self.repo, "0" * 40, self.c2))

    def test_git_error_raises(self):
        with self.assertRaises(GitError):
            git(self.repo, "show", "no-such-ref")

    def test_timeout_raises_git_error(self):
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["git"], 1)):
            with self.assertRaises(GitError):
                git(self.repo, "status")

    def test_unicode_path_not_quoted(self):
        self.assertIn("b/한글.txt", diff_text(self.repo, self.c2, self.c3, merge_base=True))


if __name__ == "__main__":
    unittest.main()
