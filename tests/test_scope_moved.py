"""A finding moved by the anchor search (file gone from head) is confirmed, never trusted."""
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

    def test_moved_finding_is_asked_not_silently_open(self):
        s, chunks, rv, _ = review(self.r, self.base, self.head, self.st)
        self.assertNotEqual(s["mode"], "unchanged")
        self.assertEqual([(p["file"], p["line"], p["pre"], p["located"], p.get("moved"))
                          for p in s["previous"]], [("o.cu", 5, "ask", False, True)])
        self.assertEqual([t["path"] for t in s["targets"]], ["o.cu"])
        self.assertTrue(hunk_heads(chunks))
        self.assertEqual(rv["event"], "COMMENT")
        self.assertEqual([p["status"] for p in rv["previous"]], ["unclear"])
        self.assertIn("이전 차단 지적 확인 필요", render_body(rv, [], META, self.st))

    def test_model_resolves_moved_finding(self):
        fid = self.st["findings"][0]["id"]
        _, _, rv, st = review(self.r, self.base, self.head, self.st,
                              reports=[(fid, "resolved")])
        self.assertEqual(rv["event"], "APPROVE")
        self.assertEqual(st["findings"], [])


class MovedIntoTargetTest(unittest.TestCase):
    def test_moved_into_review_target_unreported_is_unclear(self):
        r = Repo(self)
        base = r.commit({"k.cu": K, "a.py": PY})
        head = r.commit({"k.cu": K.replace("int k5 = 5;", "int k5 = *null;")})
        _, _, rv, st = review(r, base, head, None, finds=[bad("k.cu", 6)])
        r.git("rm", "-q", "k.cu")
        head = r.commit({"m.cu": K.replace("int k5 = 5;", "int k5 = *null;") + PAD})
        s, _, rv, _ = review(r, base, head, st)
        self.assertEqual([(p["file"], p["line"], p["pre"], p["located"]) for p in s["previous"]],
                         [("m.cu", 6, "ask", False)])
        self.assertEqual((rv["event"], [p["status"] for p in rv["previous"]]),
                         ("COMMENT", ["unclear"]))


class MovedIntoExcludedOnlyTest(unittest.TestCase):
    def test_excluded_destination_reposts_change_request(self):
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
        # not silently unchanged: the change request is re-posted at the new place
        self.assertEqual(s["mode"], "incremental")
        self.assertEqual([(p["file"], p["pre"], p["located"]) for p in s["previous"]],
                         [("vendor/module.cu", "open", False)])
        self.assertEqual(rv["event"], "REQUEST_CHANGES")
        self.assertEqual([f["file"] for f in st["findings"]], ["vendor/module.cu"])
        s, _, rv, _ = review(r, base, r.commit({}, amend=True), st)
        self.assertEqual((s["mode"], rv), ("unchanged", None))


if __name__ == "__main__":
    unittest.main()
