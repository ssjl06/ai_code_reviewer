import unittest

from review.fingerprint import fp_line
from review.ledger import CLOSED, advance, from_v2, new_issue

V2 = {"id": "abc123", "file": "a.cc", "line": 7, "anchor": "ff", "title": "T",
      "severity": "major", "blocking": True}


def issue(path, line, **kw):
    base = {"id": "i1", "file": path, "line": line, "lo": line, "hi": line, "status": "open",
            "severity": "major", "blocking": True, "anchor": "", "title": "t",
            "excerpt": "", "orphan": False}
    base.update(kw)
    return base


class LedgerTest(unittest.TestCase):
    def test_issue_follows_a_shift_and_rename(self):
        u0 = {"a.cc": {"path": "b.cc", "deleted": False, "hunks": [(1, 0, 2, 2)]}}
        out, fb = advance([issue("a.cc", 10)], u0, {})
        self.assertEqual((out[0]["file"], out[0]["line"], out[0]["lo"], out[0]["hi"]),
                         ("b.cc", 12, 12, 12))
        self.assertFalse(fb)

    def test_deleted_code_makes_an_orphan(self):
        u0 = {"a.cc": {"path": "a.cc", "deleted": False, "hunks": [(10, 1, 9, 0)]}}
        self.assertTrue(advance([issue("a.cc", 10)], u0, {})[0][0]["orphan"])

    def test_deleted_file_makes_an_orphan(self):
        u0 = {"a.cc": {"path": "a.cc", "deleted": True, "hunks": [(1, 20, 0, 0)]}}
        self.assertTrue(advance([issue("a.cc", 10)], u0, {})[0][0]["orphan"])

    def test_an_orphan_stays_an_orphan(self):
        out, _ = advance([dict(issue("a.cc", 10), orphan=True)], {}, {})
        self.assertTrue(out[0]["orphan"])

    def test_no_previous_sha_uses_the_anchor(self):
        blobs = {"a.cc": ["x"] * 30}
        i = dict(issue("a.cc", 10), anchor=fp_line(blobs["a.cc"], 10).hex())
        out, fb = advance([i], None, blobs)
        self.assertTrue(fb)
        self.assertEqual(out[0]["line"], 10)

    def test_no_previous_sha_relocates_and_shifts_the_region(self):
        lines = ["x"] * 30
        lines[14] = "unique marker"
        i = dict(issue("a.cc", 10, lo=8, hi=12), anchor=fp_line(lines, 15).hex())
        out, fb = advance([i], None, {"a.cc": lines})
        self.assertEqual((out[0]["line"], out[0]["lo"], out[0]["hi"]), (15, 13, 17))

    def test_no_previous_sha_and_no_anchor_keeps_the_region(self):
        out, fb = advance([dict(issue("a.cc", 10), anchor="")], None, {"a.cc": []})
        self.assertEqual((out[0]["lo"], out[0]["hi"]), (10, 10))
        self.assertTrue(fb)

    def test_untouched_file_keeps_its_region(self):
        out, fb = advance([issue("a.cc", 10)], {}, {})
        self.assertEqual((out[0]["line"], out[0]["file"]), (10, "a.cc"))
        self.assertFalse(fb)

    def test_advance_copies_and_keeps_identity_fields(self):
        src = issue("a.cc", 10, status="waived")
        u0 = {"a.cc": {"path": "a.cc", "deleted": False, "hunks": [(1, 0, 2, 2)]}}
        out, _ = advance([src], u0, {})
        self.assertEqual(src["line"], 10)
        for k in ("id", "status", "severity", "blocking"):
            self.assertEqual(out[0][k], src[k])

    def test_ids_never_change(self):
        self.assertEqual(from_v2(V2)["id"], V2["id"])

    def test_from_v2_and_new_issue(self):
        i = from_v2(V2)
        self.assertEqual((i["status"], i["lo"], i["hi"], i["excerpt"], i["orphan"]),
                         ("open", 7, 7, "", False))
        n = new_issue({"id": "n", "file": "a.cc", "line": 3, "anchor": "", "title": "t",
                       "severity": "minor", "blocking": False, "detail": "d" * 300})
        self.assertEqual((n["lo"], n["hi"], len(n["excerpt"]), n["status"]), (3, 3, 200, "open"))
        self.assertEqual(CLOSED, ("resolved", "waived"))


if __name__ == "__main__":
    unittest.main()
