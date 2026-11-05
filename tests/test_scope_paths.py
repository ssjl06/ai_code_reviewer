"""Previous findings on files that a later push deletes or renames again."""
import os
import unittest

from review.previous import anchor_of
from tests.test_scope_regress import Repo, review

K = "".join("int k%d = %d;\n" % (i, i) for i in range(20))
PY = "".join("v%d = %d\n" % (i, i) for i in range(20))


def bad(path, line):
    return {"file": path, "line": line, "severity": "critical", "confidence": 95,
            "category": "bug", "title": "broken", "detail": "x"}


class DeletedFileTest(unittest.TestCase):
    def test_finding_of_deleted_file_resolved(self):
        r = Repo(self)
        base = r.commit({"k.cu": K, "a.py": PY})
        head = r.commit({"k.cu": K.replace("int k5 = 5;", "int k5 = *null;"),
                         "a.py": PY + "w = 1\n"})
        _, _, rv, st = review(r, base, head, None, finds=[bad("k.cu", 6)])
        self.assertEqual(rv["event"], "REQUEST_CHANGES")
        r.git("rm", "-q", "k.cu")
        head = r.commit({})
        s, _, rv, _ = review(r, base, head, st)
        self.assertNotEqual(s["mode"], "unchanged")
        self.assertEqual([(p["status"], p["note"]) for p in rv["previous"]],
                         [("resolved", "파일이 삭제됨")])
        self.assertEqual(rv["event"], "APPROVE")


class MovedToExcludedFileTest(unittest.TestCase):
    def test_finding_moved_into_excluded_file_is_not_followed(self):
        # was test_finding_moved_into_excluded_file_stays_open: moves are not followed, so the
        # deleted file's issue is resolved (the spec's accepted cost)
        r = Repo(self)
        base = r.commit({"k.cu": K, "a.py": PY})
        head = r.commit({"k.cu": K.replace("int k5 = 5;", "int k5 = *null;"),
                         "a.py": PY + "w = 1\n"})
        _, _, rv, st = review(r, base, head, None, finds=[bad("k.cu", 6)])
        self.assertEqual(rv["event"], "REQUEST_CHANGES")
        r.git("rm", "-q", "k.cu")
        os.makedirs(os.path.join(r.d, "vendor"))
        pad = "".join("static int pad%d = %d;\n" % (i, i * 7) for i in range(200))
        head = r.commit({"vendor/module.cu": K.replace("int k5 = 5;", "int k5 = *null;") + pad,
                         "a.py": PY + "w = 2\n"})
        names = r.git("diff", "-M", "--name-status", base, head).split("\n")
        self.assertIn("D\tk.cu", names)
        s, _, rv, _ = review(r, base, head, st)
        self.assertEqual([(p["file"], p["pre"], p["located"]) for p in s["previous"]],
                         [("k.cu", "resolved", False)])
        self.assertEqual(rv["event"], "APPROVE")

    def test_moved_into_excluded_file_already_loaded_for_other_finding(self):
        r = Repo(self)
        os.makedirs(os.path.join(r.d, "vendor"))
        vend = PY.replace("v5 = 5", "v5 = *null")
        base = r.commit({"k.cu": K, "a.py": PY})
        head = r.commit({"k.cu": K.replace("int k5 = 5;", "int k5 = *null;"),
                         "vendor/module.cu": vend, "a.py": PY + "w = 1\n"})
        _, _, rv, st = review(r, base, head, None, finds=[bad("k.cu", 6)])
        self.assertEqual(rv["event"], "REQUEST_CHANGES")
        lines = vend.split("\n")
        other = dict(st["issues"][0], id="deadbeef", file="vendor/module.cu", line=6, lo=6, hi=6,
                     anchor=anchor_of({"vendor/module.cu": lines[:-1]})("vendor/module.cu", 6))
        st = dict(st, issues=st["issues"] + [other])
        r.git("rm", "-q", "k.cu")
        pad = "".join("static int pad%d = %d;\n" % (i, i * 7) for i in range(200))
        head = r.commit({"vendor/module.cu": vend + K.replace("int k5 = 5;", "int k5 = *null;")
                         + pad, "a.py": PY + "w = 2\n"})
        s, _, rv, _ = review(r, base, head, st)
        self.assertEqual(sorted((p["file"], p["pre"], p["located"]) for p in s["previous"]),
                         [("k.cu", "resolved", False), ("vendor/module.cu", "open", True)])
        self.assertEqual(rv["event"], "REQUEST_CHANGES")


class RenameChainTest(unittest.TestCase):
    def setUp(self):
        self.r = Repo(self)
        self.base = self.r.commit({"old.py": PY, "a.py": "a = 1\n"})
        self.r.git("mv", "old.py", "middle.py")
        head = self.r.commit({"middle.py": PY.replace("v5 = 5", "v5 = eval(x)")})
        _, _, rv, self.st = review(self.r, self.base, head, None, finds=[bad("middle.py", 6)])
        self.assertEqual(rv["event"], "REQUEST_CHANGES")
        self.assertEqual(self.st["issues"][0]["file"], "middle.py")
        self.r.git("mv", "middle.py", "final.py")

    def _assert_open(self, previous, located=True):
        self.assertEqual([(p["file"], p["pre"] in ("open", "ask"), p["located"], p["line"])
                          for p in previous], [("final.py", True, located, 6)])

    def test_rename_only_keeps_finding(self):
        head = self.r.commit({})
        s, _, rv, _ = review(self.r, self.base, head, self.st)
        self._assert_open(s["previous"])
        # same content already reviewed: no new review, the change request stands
        self.assertEqual((s["mode"], rv), ("unchanged", None))

    def test_rename_with_other_change_requests_changes(self):
        head = self.r.commit({"a.py": "a = 2\n"})
        s, _, rv, st = review(self.r, self.base, head, self.st)
        self._assert_open(s["previous"])
        self.assertEqual(rv["event"], "REQUEST_CHANGES")
        self.assertEqual([f["file"] for f in st["issues"]], ["final.py"])

    def test_prev_sha_unavailable_cannot_follow_a_rename_chain(self):
        # was test_prev_sha_unavailable_uses_anchor: the anchor search used to find the code in
        # final.py. Without the previous head only the PR's own renames (old.py -> final.py)
        # map a path, so middle.py is neither mapped nor in the PR. Left-the-PR cannot be told
        # from renamed here, so the issue is not resolved: it is unclear, a COMMENT.
        head = self.r.commit({})
        st = dict(self.st, sha="0" * 40)
        s, _, rv, nxt = review(self.r, self.base, head, st)
        self.assertTrue(s["weak_tracking"])
        self.assertEqual([(p["file"], p["pre"], p["located"]) for p in s["previous"]],
                         [("middle.py", "ask", False)])
        self.assertEqual((rv["event"], rv["unclear_blocking"]), ("COMMENT", True))
        self.assertEqual([(i["file"], i["status"]) for i in nxt["issues"]],
                         [("middle.py", "open")])

    def test_prev_sha_unavailable_follows_a_plain_rename(self):
        r = Repo(self)
        base = r.commit({"old.py": PY, "a.py": "a = 1\n"})
        head = r.commit({"old.py": PY.replace("v5 = 5", "v5 = eval(x)")})
        _, _, rv, st = review(r, base, head, None, finds=[bad("old.py", 6)])
        self.assertEqual(rv["event"], "REQUEST_CHANGES")
        r.git("mv", "old.py", "new.py")
        head = r.commit({})
        weak = dict(st, sha="0" * 40)
        s, _, rv, _ = review(r, base, head, weak)  # a pure rename: nothing to review, stands
        self.assertEqual((s["mode"], rv), ("unchanged", None))
        head = r.commit({"a.py": "a = 2\n"})
        s, _, rv, nxt = review(r, base, head, weak)
        self.assertTrue(s["weak_tracking"])
        self.assertEqual([(p["file"], p["line"], p["located"]) for p in s["previous"]],
                         [("new.py", 6, True)])
        self.assertEqual(rv["event"], "REQUEST_CHANGES")
        self.assertEqual([(i["file"], i["line"], i["status"]) for i in nxt["issues"]],
                         [("new.py", 6, "open")])

    def test_known_prev_sha_still_resolves_a_file_that_left_the_pr(self):
        r = Repo(self)
        base = r.commit({"k.cu": K, "a.py": PY})
        head = r.commit({"k.cu": K.replace("int k5 = 5;", "int k5 = *null;"), "a.py": PY + "w = 1\n"})
        _, _, rv, st = review(r, base, head, None, finds=[bad("k.cu", 6)])
        r.git("checkout", "-q", base, "--", "k.cu")
        head = r.commit({})
        s, _, rv, _ = review(r, base, head, st)
        self.assertFalse(s["weak_tracking"])
        self.assertEqual([(p["pre"], p["note"]) for p in s["previous"]],
                         [("resolved", "파일이 PR에서 빠짐")])
        self.assertEqual(rv["event"], "APPROVE")


if __name__ == "__main__":
    unittest.main()
