"""Code that moves between files is not followed: the issue becomes resolved or an orphan."""
import os
import unittest

from review.render import render_body
from tests.test_render import META
from tests.test_scope_paths import K, PY, bad
from tests.test_scope_regress import Repo, hunk_heads, review

# keeps git from pairing the deleted file with its destination as a rename
PAD = "".join("static int pad%d = %d;\n" % (i, i * 7) for i in range(200))

BODY = "void f() {\n  if (x) {\n    run();\n  }\n}\n"


class MovedGenericAnchorTest(unittest.TestCase):
    """The deleted file's anchor (`  }` closer) also matches an unrelated reviewed file."""

    def setUp(self):
        self.r = Repo(self)
        self.base = self.r.commit({"k.cu": "int a;\n", "o.cu": "int z;\n"})
        h1 = self.r.commit({"k.cu": "int a;\n" + BODY, "o.cu": "int z;\nint w;\n" + BODY})
        _, _, rv, self.st = review(self.r, self.base, h1, None, finds=[bad("k.cu", 4)])
        self.assertEqual(rv["event"], "REQUEST_CHANGES")
        self.r.git("rm", "-q", "k.cu")
        self.head = self.r.commit({})

    def test_the_lookalike_in_another_file_is_not_followed(self):
        # was test_moved_finding_is_asked_not_silently_open: the anchor search used to land on
        # o.cu and ask the model; now the deleted file's issue is simply resolved
        s, chunks, rv, st = review(self.r, self.base, self.head, self.st)
        self.assertNotEqual(s["mode"], "unchanged")
        self.assertEqual([(p["file"], p["line"], p["pre"], p["located"], p.get("moved"))
                          for p in s["previous"]], [("k.cu", 4, "resolved", False, None)])
        self.assertEqual(s["targets"], [])
        self.assertEqual(hunk_heads(chunks), [])
        self.assertEqual(rv["event"], "APPROVE")
        self.assertEqual([(p["status"], p["note"]) for p in rv["previous"]],
                         [("resolved", "파일이 삭제됨")])
        self.assertNotIn("이전 차단 지적 확인 필요", render_body(rv, [], META, st))
        self.assertEqual([(i["file"], i["status"], i.get("orphan")) for i in st["issues"]],
                         [("k.cu", "resolved", True)])

    def test_a_report_for_the_deleted_issue_changes_nothing(self):
        # was test_model_resolves_moved_finding: nothing to confirm any more
        fid = self.st["issues"][0]["id"]
        _, _, rv, st = review(self.r, self.base, self.head, self.st,
                              reports=[(fid, "resolved")])
        self.assertEqual(rv["event"], "APPROVE")
        self.assertEqual([i["status"] for i in st["issues"]], ["resolved"])


class MovedIntoTargetTest(unittest.TestCase):
    def setUp(self):
        self.r = Repo(self)
        self.base = self.r.commit({"k.cu": K, "a.py": PY})
        self.moved = K.replace("int k5 = 5;", "int k5 = *null;")
        head = self.r.commit({"k.cu": self.moved})
        _, _, rv, self.st = review(self.r, self.base, head, None, finds=[bad("k.cu", 6)])
        self.assertEqual(rv["event"], "REQUEST_CHANGES")
        self.r.git("rm", "-q", "k.cu")
        self.head = self.r.commit({"m.cu": self.moved + PAD})

    def test_moved_into_review_target_is_not_followed(self):
        # was test_moved_into_review_target_unreported_is_unclear
        s, _, rv, st = review(self.r, self.base, self.head, self.st)
        self.assertEqual([(p["file"], p["line"], p["pre"]) for p in s["previous"]],
                         [("k.cu", 6, "resolved")])
        self.assertEqual((rv["event"], [p["status"] for p in rv["previous"]]),
                         ("APPROVE", ["resolved"]))

    def test_the_defect_is_reported_again_at_the_new_place(self):
        # accepted cost of not following moves: it fails toward showing the defect
        _, _, rv, st = review(self.r, self.base, self.head, self.st, finds=[bad("m.cu", 6)])
        self.assertEqual(rv["event"], "REQUEST_CHANGES")
        self.assertEqual([(f["file"], f["line"]) for f in rv["findings"]], [("m.cu", 6)])
        self.assertEqual([(i["file"], i["status"]) for i in st["issues"]],
                         [("m.cu", "open"), ("k.cu", "resolved")])

    def test_same_as_to_an_issue_resolved_with_its_file_does_not_swallow_the_finding(self):
        fid = self.st["issues"][0]["id"]
        _, _, rv, st = review(self.r, self.base, self.head, self.st,
                              finds=[dict(bad("m.cu", 6), same_as=fid)])
        self.assertEqual([f["file"] for f in rv["findings"]], ["m.cu"])
        self.assertEqual(rv["duplicates"], [])


class MovedIntoExcludedOnlyTest(unittest.TestCase):
    def test_excluded_destination_is_not_followed(self):
        # was test_excluded_destination_reposts_change_request: the anchor search used to
        # follow the code into vendor/ and re-post the change request. Accepted cost of the
        # spec: moves are not followed, so the deleted file's issue is resolved.
        r = Repo(self)
        os.makedirs(os.path.join(r.d, "vendor"))
        base = r.commit({"k.cu": K, "a.py": PY})
        bad_k = K.replace("int k5 = 5;", "int k5 = *null;")
        head = r.commit({"k.cu": bad_k, "vendor/module.cu": bad_k + PAD})
        _, _, rv, st = review(r, base, head, None, finds=[bad("k.cu", 6)])
        self.assertEqual(rv["event"], "REQUEST_CHANGES")
        r.git("rm", "-q", "k.cu")
        head = r.commit({})
        s, _, rv, st = review(r, base, head, st)
        self.assertEqual(s["mode"], "incremental")
        self.assertEqual([(p["file"], p["pre"]) for p in s["previous"]], [("k.cu", "resolved")])
        self.assertEqual([i["status"] for i in st["issues"]], ["resolved"])
        s, _, rv, _ = review(r, base, r.commit({}, amend=True), st)
        self.assertEqual((s["mode"], rv), ("unchanged", None))


if __name__ == "__main__":
    unittest.main()
