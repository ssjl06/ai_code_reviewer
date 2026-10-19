import json
import os
import subprocess
import tempfile
import unittest

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
    """StateV2 after a review in which every chunk was ok."""
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


if __name__ == "__main__":
    unittest.main()
