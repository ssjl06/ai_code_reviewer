import json
import os
import subprocess
import tempfile
import unittest

from review.aggregate import merge
from review.config import load_config
from review.diff_split import build_chunks
from review.fingerprint import fp_line
from review.scope import determine, next_state
from review.validate import normalize_state

CFG = load_config()


def code(tag, n=10):
    return "".join("%s_%d = %d\n" % (tag, i, i) for i in range(1, n + 1))


def run(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                          text=True).stdout.strip()


def state_for(scope, head, findings=(), complete=True):
    """State v3 after a review in which every chunk was ok."""
    chunks, _, _ = build_chunks(scope["targets"], CFG)
    results = [{"index": c["index"], "status": "ok"} for c in chunks]
    prev = [dict(p, status="open") for p in scope["previous"] if p["pre"] != "resolved"]
    review = {"findings": list(findings), "previous": prev, "complete": complete}
    st = next_state(head, review, scope, chunks, results)
    return normalize_state(json.loads(json.dumps(st)))


class ScopeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.R = tmp.name
        run(self.R, "init", "-q", "-b", "main")
        run(self.R, "config", "user.email", "t@example.com")
        run(self.R, "config", "user.name", "t")
        self.BASE = self.branch({"base.py": code("base")})
        run(self.R, "checkout", "-q", "-b", "feat")

    def branch(self, files, rm=()):
        for name in rm:
            run(self.R, "rm", "-q", name)
        for name, content in files.items():
            with open(os.path.join(self.R, name), "w", encoding="utf-8") as f:
                f.write(content)
            run(self.R, "add", name)
        run(self.R, "commit", "-q", "-m", "m")
        return run(self.R, "rev-parse", "HEAD")

    def reviewed(self, head, findings=()):
        return state_for(determine(self.R, self.BASE, head, None, CFG), head, findings)

    def advance_main(self):
        run(self.R, "checkout", "-q", "main")
        main2 = self.branch({"other.py": code("other")})
        run(self.R, "checkout", "-q", "feat")
        return main2

    def test_first_review_full(self):
        h = self.branch({"a.py": code("a"), "b.py": code("b")})
        s = determine(self.R, self.BASE, h, None, CFG)
        self.assertEqual((s["mode"], s["range"]), ("full", "%s...%s" % (self.BASE[:7], h[:7])))
        self.assertEqual(s["changed"], {"a.py": set(range(1, 11)), "b.py": set(range(1, 11))})
        self.assertEqual({f["path"] for f in s["targets"]}, {"a.py", "b.py"})
        self.assertEqual(s["reviewable"], 2)

    def test_same_sha_complete_unchanged(self):
        h = self.branch({"a.py": code("a")})
        s = determine(self.R, self.BASE, h, self.reviewed(h), CFG)
        self.assertEqual((s["mode"], s["targets"]), ("unchanged", []))

    def test_same_sha_incomplete_reviews_again(self):
        h = self.branch({"a.py": code("a")})
        st = dict(self.reviewed(h), complete=False, seen={})
        s = determine(self.R, self.BASE, h, st, CFG)
        self.assertEqual(s["mode"], "incremental")
        self.assertEqual([f["path"] for f in s["targets"]], ["a.py"])

    def test_squash_unchanged(self):
        self.branch({"a.py": code("a")})
        st = self.reviewed(self.branch({"b.py": code("b")}))
        run(self.R, "reset", "-q", "--soft", self.BASE)
        h = self.branch({})
        self.assertEqual(determine(self.R, self.BASE, h, st, CFG)["mode"], "unchanged")

    def test_rebase_onto_advanced_main_unchanged(self):
        st = self.reviewed(self.branch({"a.py": code("a")}))
        main2 = self.advance_main()
        run(self.R, "rebase", "-q", "main")
        s = determine(self.R, main2, run(self.R, "rev-parse", "HEAD"), st, CFG)
        self.assertEqual(s["mode"], "unchanged")
        self.assertNotIn("other.py", [f["path"] for f in s["pr_files"]])

    def test_merge_main_unchanged(self):
        st = self.reviewed(self.branch({"a.py": code("a")}))
        main2 = self.advance_main()
        run(self.R, "merge", "-q", "--no-edit", "main")
        s = determine(self.R, main2, run(self.R, "rev-parse", "HEAD"), st, CFG)
        self.assertEqual(s["mode"], "unchanged")

    def test_rebase_with_edit_only_edit(self):
        st = self.reviewed(self.branch({"a.py": code("a"), "b.py": code("b")}))
        main2 = self.advance_main()
        run(self.R, "rebase", "-q", "main")
        h = self.branch({"a.py": code("a").replace("a_5 = 5", "a_5 = 55")})
        s = determine(self.R, main2, h, st, CFG)
        self.assertEqual((s["mode"], [f["path"] for f in s["targets"]]), ("incremental", ["a.py"]))
        self.assertEqual(s["changed"], {"a.py": {4, 5, 6}})

    def test_rename_without_edit_unchanged(self):
        st = self.reviewed(self.branch({"base.py": code("base").replace("_3 = 3", "_3 = 33")}))
        run(self.R, "mv", "base.py", "moved.py")
        h = self.branch({})
        s = determine(self.R, self.BASE, h, st, CFG)
        self.assertEqual(s["pr_files"][0]["old_path"], "base.py")
        self.assertEqual(s["mode"], "unchanged")

    def test_neighbor_change_reviews_added_line(self):
        added = code("base").replace("base_4 = 4\n", "base_4 = 4\nnew = 1\n")
        st = self.reviewed(self.branch({"base.py": added}))
        h = self.branch({"base.py": added.replace("base_4 = 4", "base_4 = 44")})
        s = determine(self.R, self.BASE, h, st, CFG)
        self.assertEqual(s["changed"], {"base.py": {4, 5}})  # 5 = unchanged "new = 1"

    def test_deletion_only_followup(self):
        edited = code("base").replace("_2 = 2", "_2 = 22")
        st = self.reviewed(self.branch({"base.py": edited}))
        h = self.branch({"base.py": edited.replace("base_7 = 7\n", "")})
        s = determine(self.R, self.BASE, h, st, CFG)
        self.assertEqual((s["mode"], s["deleted"], s["added"]),
                         ("incremental", {"base.py": {7}}, {"base.py": set()}))
        self.assertEqual(s["changed"], {"base.py": {7}})

    def test_duplicate_paste_reviews_both_copies(self):
        text = code("a", 20).replace("a_2 = 2\n", "dup_a\ndup_b\ndup_c\n")
        st = self.reviewed(self.branch({"a.py": text}))
        h = self.branch({"a.py": text + "dup_a\ndup_b\ndup_c\n"})
        s = determine(self.R, self.BASE, h, st, CFG)
        self.assertEqual(s["changed"], {"a.py": {3, 22, 23, 24, 25}})  # 3, 24 = dup_b copies

    def test_dropped_file_resolves_previous(self):
        h = self.branch({"a.py": code("a"), "b.py": code("b")})
        lines = code("b").splitlines()
        f = {"id": "f1", "file": "b.py", "line": 2, "anchor": fp_line(lines, 2).hex(),
             "title": "t", "severity": "major", "blocking": True}
        st = self.reviewed(h, [f])
        s = determine(self.R, self.BASE, self.branch({}, rm=["b.py"]), st, CFG)
        self.assertEqual((s["mode"], s["targets"]), ("incremental", []))
        self.assertEqual([(p["id"], p["pre"]) for p in s["previous"]], [("f1", "resolved")])

    def test_previous_located_in_target(self):
        h = self.branch({"a.py": code("a")})
        lines = code("a").splitlines()
        f = {"id": "f1", "file": "a.py", "line": 8, "anchor": fp_line(lines, 8).hex(),
             "title": "t", "severity": "major", "blocking": True}
        st = self.reviewed(h, [f])
        s = determine(self.R, self.BASE, self.branch({"a.py": "top = 0\n" + code("a")}), st, CFG)
        self.assertEqual([(p["pre"], p["located"], p["line"]) for p in s["previous"]],
                         [("ask", True, 9)])


    def test_issue_follows_a_rename_and_shift(self):
        h1 = self.branch({"a.py": code("a", 30)})
        lines = code("a", 30).splitlines()
        f = {"id": "f1", "file": "a.py", "line": 20, "anchor": fp_line(lines, 20).hex(),
             "title": "t", "severity": "major", "blocking": True}
        st = self.reviewed(h1, [f])
        run(self.R, "mv", "a.py", "b.py")
        h2 = self.branch({"b.py": "top = 0\ntop = 1\ntop = 2\n" + code("a", 30)})
        s = determine(self.R, self.BASE, h2, st, CFG)
        self.assertFalse(s["weak_tracking"])
        self.assertEqual([(p["id"], p["file"], p["line"], p["lo"], p["hi"], p["located"])
                          for p in s["previous"]], [("f1", "b.py", 23, 23, 23, True)])

    def test_v2_state_gets_no_duplicate_comment(self):
        h = self.branch({"a.py": code("a")})
        lines = code("a").splitlines()
        v2 = {"v": 2, "sha": h, "complete": False, "seen": {}, "seen_files": {},
              "findings": [{"id": "f1", "file": "a.py", "line": 8,
                            "anchor": fp_line(lines, 8).hex(), "title": "old",
                            "severity": "major", "blocking": True}]}
        s = determine(self.R, self.BASE, h, normalize_state(v2), CFG)
        chunks, _, _ = build_chunks(s["targets"], CFG)
        again = {"file": "a.py", "line": 8, "severity": "minor", "confidence": 90,
                 "category": "bug", "title": "reworded", "detail": "d"}
        out = {"summary": "", "findings": [again], "incomplete_reason": None,
               "previous_findings": []}
        results = [{"index": c["index"], "status": "ok", "output": out} for c in chunks]
        rv = merge(results, CFG, s["previous"], s["changed"], False, closed=s["closed"],
                   chunk_files={c["index"]: c["files"] for c in chunks})
        self.assertEqual(rv["findings"], [])
        self.assertEqual([d["dup_of"] for d in rv["duplicates"]], ["f1"])
        self.assertEqual((rv["event"], [p["id"] for p in rv["previous"]]),
                         ("REQUEST_CHANGES", ["f1"]))

    def test_missing_previous_sha_does_not_fail(self):
        h = self.branch({"a.py": code("a")})
        lines = code("a").splitlines()
        f = {"id": "f1", "file": "a.py", "line": 8, "anchor": fp_line(lines, 8).hex(),
             "title": "t", "severity": "major", "blocking": True}
        st = dict(self.reviewed(h, [f]), sha="0" * 40)
        h2 = self.branch({"a.py": "top = 0\n" + code("a")})
        s = determine(self.R, self.BASE, h2, st, CFG)
        self.assertTrue(s["weak_tracking"])
        self.assertEqual([(p["id"], p["line"], p["located"]) for p in s["previous"]],
                         [("f1", 9, True)])  # found by its anchor
        st["issues"][0]["anchor"] = fp_line(["other"], 1).hex()  # an anchor that matches nothing
        s = determine(self.R, self.BASE, h2, st, CFG)
        self.assertEqual([(p["line"], p["located"]) for p in s["previous"]], [(8, False)])

    def test_known_previous_sha_is_not_weak(self):
        h = self.branch({"a.py": code("a")})
        s = determine(self.R, self.BASE, self.branch({"b.py": code("b")}), self.reviewed(h), CFG)
        self.assertFalse(s["weak_tracking"])

    def test_closed_issues_move_with_the_code_and_leave_with_the_file(self):
        h1 = self.branch({"a.py": code("a", 30), "b.py": code("b")})
        closed = [dict(i, status="waived") for i in [
            {"id": "w1", "file": "a.py", "line": 20, "lo": 19, "hi": 21, "severity": "major",
             "blocking": False, "anchor": "", "title": "", "excerpt": "", "orphan": False},
            {"id": "w2", "file": "b.py", "line": 3, "lo": 3, "hi": 3, "severity": "major",
             "blocking": False, "anchor": "", "title": "", "excerpt": "", "orphan": False}]]
        st = dict(self.reviewed(h1), issues=closed)
        h2 = self.branch({"a.py": "top = 0\n" + code("a", 30)}, rm=["b.py"])
        s = determine(self.R, self.BASE, h2, st, CFG)
        self.assertEqual(s["previous"], [])
        self.assertEqual([(c["id"], c["status"], c["line"], c["lo"], c["hi"], c["orphan"])
                          for c in s["closed"]],
                         [("w1", "waived", 21, 20, 22, False), ("w2", "waived", 3, 3, 3, True)])


if __name__ == "__main__":
    unittest.main()
