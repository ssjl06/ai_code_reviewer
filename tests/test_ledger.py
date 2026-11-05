import unittest

from review.fingerprint import fp_line
from review.ledger import CLOSED, advance, classify, finding_id, from_v2, new_issue, unique_id

V2 = {"id": "abc123", "file": "a.cc", "line": 7, "anchor": "ff", "title": "T",
      "severity": "major", "blocking": True}


def issue(path, line, **kw):
    base = {"id": "i1", "file": path, "line": line, "lo": line, "hi": line, "status": "open",
            "severity": "major", "blocking": True, "anchor": "", "title": "t",
            "excerpt": "", "orphan": False}
    base.update(kw)
    return base


def finding(path, line, **kw):
    base = {"file": path, "line": line, "severity": "major", "confidence": 90,
            "category": "bug", "title": "t", "detail": "d"}
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

class ClassifyTest(unittest.TestCase):
    def test_reworded_finding_near_an_open_issue_is_merged(self):
        new, dups, _ = classify([finding("a.cc", 12, title="다른 표현")], [issue("a.cc", 10)])
        self.assertEqual((new, len(dups)), ([], 1))
        self.assertEqual(dups[0]["dup_of"], "i1")

    def test_finding_four_lines_away_is_new(self):
        self.assertEqual(len(classify([finding("a.cc", 14)], [issue("a.cc", 10)])[0]), 1)

    def test_region_edges_count(self):
        i = dict(issue("a.cc", 10), lo=8, hi=12)
        self.assertEqual(classify([finding("a.cc", 15)], [i])[0], [])
        self.assertEqual(len(classify([finding("a.cc", 16)], [i])[0]), 1)

    def test_missing_lo_hi_status_orphan_default(self):
        i = {k: v for k, v in issue("a.cc", 10).items() if k not in ("lo", "hi", "status", "orphan")}
        self.assertEqual(classify([finding("a.cc", 13)], [i])[0], [])

    def test_closed_match_is_dropped_even_as_critical(self):
        new, dups, _ = classify([finding("a.cc", 10, severity="critical")],
                                [dict(issue("a.cc", 10), status="resolved")])
        self.assertEqual((new, len(dups)), ([], 1))

    def test_open_issue_is_preferred_over_closed(self):
        issues = [dict(issue("a.cc", 10, id="c"), status="waived"), issue("a.cc", 12, id="o")]
        self.assertEqual(classify([finding("a.cc", 10)], issues)[1][0]["dup_of"], "o")

    def test_same_as_within_15_lines_links(self):
        new, _, links = classify([finding("a.cc", 25, same_as="i1")], [issue("a.cc", 10, id="i1")])
        self.assertEqual((new, links), ([], [{"id": "i1", "file": "a.cc", "line": 25}]))

    def test_same_as_beyond_15_lines_is_ignored(self):
        self.assertEqual(len(classify([finding("a.cc", 26, same_as="i1")],
                                      [issue("a.cc", 10, id="i1")])[0]), 1)

    def test_same_as_in_another_file_is_ignored(self):
        self.assertEqual(len(classify([finding("b.cc", 10, same_as="i1")],
                                      [issue("a.cc", 10, id="i1")])[0]), 1)

    def test_same_as_closed_beyond_limit_is_ignored_but_orphan_is_not(self):
        closed = dict(issue("a.cc", 10, id="i1"), status="resolved")
        self.assertEqual(len(classify([finding("a.cc", 40, same_as="i1")], [closed])[0]), 1)
        new, dups, _ = classify([finding("a.cc", 12, same_as="i1")], [closed])
        self.assertEqual((new, len(dups)), ([], 1))

    def test_same_as_links_an_orphan_anywhere(self):
        new, _, links = classify([finding("b.cc", 500, same_as="i1")],
                                 [dict(issue("a.cc", 10, id="i1"), orphan=True)])
        self.assertEqual((new, links), ([], [{"id": "i1", "file": "b.cc", "line": 500}]))

    def test_position_never_matches_an_orphan(self):
        self.assertEqual(len(classify([finding("a.cc", 10)],
                                      [dict(issue("a.cc", 10), orphan=True)])[0]), 1)

    def test_unknown_same_as_is_kept_as_new(self):
        self.assertEqual(len(classify([finding("a.cc", 10, same_as="nope")], [])[0]), 1)

    def test_same_as_does_not_mutate_input(self):
        f = finding("a.cc", 10)
        classify([f], [issue("a.cc", 10)])
        self.assertNotIn("dup_of", f)


class UniqueIdTest(unittest.TestCase):
    def test_free_id_is_the_finding_id(self):
        f = finding("a.cc", 1)
        self.assertEqual(unique_id(f, set()), finding_id(f))

    def test_taken_id_is_rehashed(self):
        f = finding("a.cc", 1)
        first = unique_id(f, set())
        second = unique_id(f, {first})
        self.assertNotEqual(first, second)
        self.assertEqual(len(second), 8)
        self.assertNotEqual(unique_id(f, {first, second}), second)


if __name__ == "__main__":
    unittest.main()
