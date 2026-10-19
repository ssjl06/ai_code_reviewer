"""Regressions from the final review: oversized hunks, first-review scope, pure-deletion fix."""
import json
import os
import subprocess
import tempfile
import unittest

from review.aggregate import merge
from review.config import load_config
from review.diff_split import build_chunks, split_hunks
from review.previous import anchor_of
from review.render import render_body
from review.scope import determine, next_state
from review.validate import normalize_state
from tests.test_render import META

CFG = load_config()


class Repo:
    def __init__(self, test):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.d = tmp.name
        for args in (("init", "-q"), ("config", "user.email", "t@x"), ("config", "user.name", "t")):
            self.git(*args)

    def git(self, *args):
        return subprocess.run(["git", "-C", self.d, *args], check=True, capture_output=True,
                              text=True).stdout.strip()

    def commit(self, files, amend=False):
        for name, text in files.items():
            with open(os.path.join(self.d, name), "w", encoding="utf-8") as f:
                f.write(text)
        self.git("add", ".")
        self.git("commit", "-q", "--allow-empty", "-m", "m", *(["--amend"] if amend else []))
        return self.git("rev-parse", "HEAD")


def review(repo, base, head, prev, cfg=CFG, finds=(), reports=()):
    s = determine(repo.d, base, head, prev, cfg)
    if s["mode"] == "unchanged":
        return s, [], None, None
    chunks, skipped, cut = build_chunks(s["targets"], cfg)
    out = {"summary": "", "findings": list(finds), "incomplete_reason": None,
           "previous_findings": [{"id": i, "status": st} for i, st in reports]}
    results = [{"index": c["index"], "status": "ok", "output": out} for c in chunks]
    rv = merge(results, cfg, s["previous"], s["changed"], s["reviewable"] == 0,
               skipped=skipped, anchor=anchor_of(s["blobs"]), truncated=cut,
               chunk_files={c["index"]: c["files"] for c in chunks})
    st = next_state(head, rv, s, chunks, results)
    return s, chunks, rv, normalize_state(json.loads(json.dumps(st)))


def hunk_heads(chunks):
    return [h.split("\n", 1)[0] for c in chunks for h in split_hunks(c["diff"])[1]]


class OversizedHunkTest(unittest.TestCase):
    def setUp(self):
        self.r = Repo(self)
        self.base = self.r.commit({"README.md": "r\n"})

    def test_big_new_file_split_and_resumed(self):
        text = "".join("x_%05d = compute_value(%05d)  # pad\n" % (i, i) for i in range(3000))
        head = self.r.commit({"big.py": text})
        _, chunks, rv, _ = review(self.r, self.base, head, None)
        self.assertGreater(len(chunks), 1)
        self.assertNotIn("[truncated", "".join(c["diff"] for c in chunks))
        self.assertEqual((rv["event"], rv["complete"]), ("APPROVE", True))
        one = dict(CFG, max_chunks=1)
        _, first, rv, st = review(self.r, self.base, head, None, one)
        self.assertEqual((rv["event"], rv["complete"], st["complete"]), ("COMMENT", False, False))
        head = self.r.commit({}, amend=True)
        _, second, rv, _ = review(self.r, self.base, head, st, one)
        self.assertEqual(hunk_heads(second)[0], hunk_heads(chunks)[1])
        self.assertNotIn(hunk_heads(first)[0], hunk_heads(second))

    def test_single_huge_line_truncates_comment(self):
        head = self.r.commit({"one.py": "x = '%s'\n" % ("y" * 90000)})
        _, chunks, rv, st = review(self.r, self.base, head, None)
        self.assertEqual((rv["event"], rv["truncated"], st["complete"]), ("COMMENT", ["one.py"], False))
        self.assertIn("잘린 변경 있음", render_body(rv, [], META, st))


class FirstReviewScopeTest(unittest.TestCase):
    def test_first_review_ignores_lines_outside_pr(self):
        r = Repo(self)
        base = r.commit({"caller.py": "import lib\nlib.f(1, 2)\n", "lib.py": "def f(a, b):\n    return a\n"})
        head = r.commit({"lib.py": "def f(a):\n    return a\n"})
        bad = {"file": "caller.py", "line": 2, "severity": "critical", "confidence": 95,
               "category": "bug", "title": "caller now broken", "detail": "x"}
        s, _, rv, st = review(r, base, head, None, finds=[bad, dict(bad, file="lib.py", line=1)])
        self.assertEqual(s["changed"], {"lib.py": {1, 2}})
        self.assertEqual([f["file"] for f in rv["findings"]], ["lib.py"])
        self.assertEqual([f["file"] for f in st["findings"]], ["lib.py"])


class PureDeletionFixTest(unittest.TestCase):
    def setUp(self):
        self.r = Repo(self)
        lines = ["line%d = %d" % (i, i) for i in range(40)]
        self.write = lambda ls: self.r.commit({"m.py": "\n".join(ls) + "\n"})
        self.base = self.write(lines)
        head = self.write(lines[:5] + ["good = 1"] + lines[5:30] + ["bad = eval(x)"] + lines[30:])
        bad = {"file": "m.py", "line": 32, "severity": "critical", "confidence": 95,
               "category": "security", "title": "eval", "detail": "x"}
        _, _, rv, self.st = review(self.r, self.base, head, None, finds=[bad])
        self.assertEqual(rv["event"], "REQUEST_CHANGES")
        self.head = self.write(lines[:5] + ["good = 1"] + lines[5:])

    def test_unreported_is_unclear_comment(self):
        s, chunks, rv, _ = review(self.r, self.base, self.head, self.st)
        self.assertEqual(s["mode"], "incremental")
        self.assertEqual([(p["pre"], p["located"]) for p in s["previous"]], [("ask", False)])
        self.assertEqual((s["changed"], len(chunks)), ({}, 1))
        self.assertEqual((rv["event"], rv["unclear_blocking"]), ("COMMENT", True))
        self.assertIn("이전 차단 지적 확인 필요", render_body(rv, [], META, self.st))

    def test_reported_resolved_approves(self):
        fid = self.st["findings"][0]["id"]
        _, _, rv, st = review(self.r, self.base, self.head, self.st, reports=[(fid, "resolved")])
        self.assertEqual((rv["event"], st["findings"]), ("APPROVE", []))


if __name__ == "__main__":
    unittest.main()
