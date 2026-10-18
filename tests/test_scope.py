import json
import os
import subprocess
import tempfile
import unittest

from review.scope import determine, next_state, parse_overrides, read_state_file


def run(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                          text=True).stdout.strip()


def commit(repo, files):
    for name, content in files.items():
        path = os.path.join(repo, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        run(repo, "add", name)
    run(repo, "commit", "-q", "-m", "m")
    return run(repo, "rev-parse", "HEAD")


class ScopeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        R = cls.R = cls._tmp.name
        run(R, "init", "-q")
        run(R, "config", "user.email", "t@example.com")
        run(R, "config", "user.name", "t")
        cls.BASE = commit(R, {"base.py": "b\n"})
        cls.C1 = commit(R, {"only_in_c1.py": "one\n"})
        cls.C2 = commit(R, {"only_in_c2.py": "two\n"})
        run(R, "checkout", "-q", "-b", "rewrite", cls.BASE)
        cls.C2_REWRITTEN = commit(R, {"rewritten.py": "r\n"})

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_full_without_prev(self):
        s = determine(self.R, self.BASE, self.C2, None)
        self.assertEqual(s["mode"], "full")
        self.assertEqual(s["range"], "%s...%s" % (self.BASE[:7], self.C2[:7]))
        self.assertEqual({f["path"] for f in s["targets"]}, {"only_in_c1.py", "only_in_c2.py"})
        self.assertIsNone(s["changed"])
        self.assertIsNone(s["delta_files"])

    def test_unchanged(self):
        s = determine(self.R, self.BASE, self.C2, {"sha": self.C2, "findings": []})
        self.assertEqual(s["mode"], "unchanged")

    def test_incremental(self):
        s = determine(self.R, self.BASE, self.C2, {"sha": self.C1, "findings": []})
        self.assertEqual((s["mode"], s["range"]), ("incremental", f"{self.C1[:7]}..{self.C2[:7]}"))
        self.assertEqual({f["path"] for f in s["targets"]}, {"only_in_c2.py"})
        self.assertIn("only_in_c1.py", {f["path"] for f in s["pr_files"]})
        self.assertEqual(s["changed"], {"only_in_c2.py": {1}})
        self.assertEqual(s["delta_files"], {"only_in_c2.py"})

    def test_force_push_falls_back_to_full(self):
        s = determine(self.R, self.BASE, self.C2_REWRITTEN, {"sha": self.C1, "findings": []})
        self.assertEqual(s["mode"], "full")
        self.assertIsNone(s["changed"])
        self.assertEqual(s["delta_files"], {"rewritten.py"})

    def test_empty_prev_sha_is_no_prev(self):
        s = determine(self.R, self.BASE, self.C2, {"sha": "", "findings": []})
        self.assertEqual((s["mode"], s["delta_files"]), ("full", None))


class HelpersTest(unittest.TestCase):
    def test_parse_overrides(self):
        got = parse_overrides(["max_chunks=2", "claude_bin=/a/b", "exclude_globs=[\"x\"]"])
        self.assertEqual(got, {"max_chunks": 2, "claude_bin": "/a/b", "exclude_globs": ["x"]})
        with self.assertRaises(ValueError):
            parse_overrides(["novalue"])

    def test_next_state(self):
        new = {"id": "n1", "file": "a", "line": 1, "title": "t", "severity": "major",
               "blocking": True, "confidence": 90, "detail": "d"}
        prev = [dict(new, id="p1", status="open"), dict(new, id="p2", status="resolved"),
                dict(new, id="p3", status="unclear", note="x")]
        review = {"findings": [new], "previous": prev, "failed_all": False}
        st = next_state("h", review, None)
        self.assertEqual(st["sha"], "h")
        self.assertEqual([f["id"] for f in st["findings"]], ["n1", "p1", "p3"])
        self.assertEqual(set(st["findings"][2]), {"id", "file", "line", "title", "severity",
                                                  "blocking"})

    def test_next_state_failed_all_keeps_previous(self):
        review = {"findings": [], "previous": [], "failed_all": True}
        prev = {"sha": "old", "findings": [{"id": "p"}]}
        self.assertEqual(next_state("h", review, prev), prev)
        self.assertEqual(next_state("h", review, None), {"sha": "", "findings": []})

    def test_read_state_file(self):
        self.assertIsNone(read_state_file(None))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            with open(p, "w") as f:
                json.dump({"sha": "x", "findings": []}, f)
            self.assertEqual(read_state_file(p)["sha"], "x")
