import unittest

from review.fingerprint import fp_line
from review.ledger import (CLOSED, advance, apply_waivers, classify, finding_id, from_v2,
                           moved_lookups, new_issue, unique_id)

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

class FoldedTest(unittest.TestCase):
    def test_a_position_merge_into_an_open_issue_is_folded(self):
        new, dups, links = classify([finding("a.cc", 11)], [issue("a.cc", 10)])
        self.assertEqual((new, links), ([], []))
        self.assertEqual([(d["dup_of"], d.get("folded")) for d in dups], [("i1", True)])

    def test_same_as_duplicates_and_links_are_not_folded(self):
        closed = issue("a.cc", 10, id="c1", status="resolved")
        _, dups, _ = classify([finding("a.cc", 11, same_as="c1")], [closed])
        self.assertEqual([(d["dup_of"], d.get("folded")) for d in dups], [("c1", None)])
        new, dups, links = classify([finding("a.cc", 11, same_as="i1")], [issue("a.cc", 10)])
        self.assertEqual((new, dups, [x["id"] for x in links]), ([], [], ["i1"]))


class MovedRegionTest(unittest.TestCase):
    """An open issue whose whole region was deleted is searched in the same file at head."""
    OLD = ["a", "b", "c", "x", "y", "d"]
    U0 = {"a.cc": {"path": "a.cc", "deleted": False, "hunks": [(4, 2, 3, 0)]}}

    def run_it(self, i, head, old=None, u0=None):
        old = {"a.cc": self.OLD} if old is None else old
        return advance([i], u0 or self.U0, head, old)[0][0]

    def where(self, i):
        return (i["file"], i["line"], i["lo"], i["hi"], i["orphan"], i["status"])

    def test_an_open_issue_relocates_with_the_primary_line_at_the_same_offset(self):
        i = self.run_it(issue("a.cc", 5, lo=4, hi=5), {"a.cc": ["a", "b", "c", "d", "x", "y"]})
        self.assertEqual(self.where(i), ("a.cc", 6, 5, 6, False, "open"))

    def test_the_hit_nearest_the_old_line_wins(self):
        head = ["x", "y", "a", "b", "c", "d", "1", "2", "3", "x", "y"]
        i = self.run_it(issue("a.cc", 4, lo=4, hi=5), {"a.cc": head})
        self.assertEqual(self.where(i), ("a.cc", 1, 1, 2, False, "open"))
        head = ["1"] * 3 + ["x", "y"] + ["2"] * 40 + ["x", "y"]
        i = self.run_it(issue("a.cc", 4, lo=4, hi=5), {"a.cc": head})
        self.assertEqual(self.where(i), ("a.cc", 4, 4, 5, False, "open"))

    def test_lines_that_are_not_contiguous_at_head_are_not_a_hit(self):
        i = self.run_it(issue("a.cc", 4, lo=4, hi=5), {"a.cc": ["x", "new", "y", "a"]})
        self.assertTrue(i["orphan"])

    def test_a_changed_line_is_not_a_hit(self):
        i = self.run_it(issue("a.cc", 4), {"a.cc": ["a", "b", "c", "d", " x"]})
        self.assertTrue(i["orphan"])

    def test_trailing_whitespace_does_not_hide_a_move(self):
        i = self.run_it(issue("a.cc", 5, lo=4, hi=5),
                        {"a.cc": ["a", "b", "c", "d", "x  ", "y\t"]})
        self.assertEqual(self.where(i), ("a.cc", 6, 5, 6, False, "open"))
        old = {"a.cc": ["a", "b", "c", "x ", "y", "d"]}
        i = self.run_it(issue("a.cc", 5, lo=4, hi=5), {"a.cc": ["d", "x", "y"]}, old=old)
        self.assertEqual(self.where(i), ("a.cc", 3, 2, 3, False, "open"))

    # an edited region line: the 3-line window of the primary line still finds the move
    WIN_OLD = ["p", "q", "r", "s", "t", "u", "v"]
    WIN_U0 = {"a.cc": {"path": "a.cc", "deleted": False, "hunks": [(2, 5, 1, 0)]}}

    def test_the_primary_window_relocates_when_a_region_line_was_edited(self):
        head = ["n0", "n1", "p", "Q", "r", "s  ", "t", "u", "v"]
        i = self.run_it(issue("a.cc", 4, lo=2, hi=6), {"a.cc": head},
                        old={"a.cc": self.WIN_OLD}, u0=self.WIN_U0)
        self.assertEqual(self.where(i), ("a.cc", 6, 4, 8, False, "open"))

    def test_the_window_hit_nearest_the_old_line_wins_and_the_region_is_clipped(self):
        head = ["r", "s", "t"] + ["z"] * 20 + ["r", "s", "t"]
        i = self.run_it(issue("a.cc", 4, lo=2, hi=6), {"a.cc": head},
                        old={"a.cc": self.WIN_OLD}, u0=self.WIN_U0)
        self.assertEqual(self.where(i), ("a.cc", 2, 1, 4, False, "open"))
        i = self.run_it(issue("a.cc", 4, lo=2, hi=6), {"a.cc": ["r", "s", "t"]},
                        old={"a.cc": self.WIN_OLD}, u0=self.WIN_U0)
        self.assertEqual(self.where(i), ("a.cc", 2, 1, 3, False, "open"))

    def test_a_region_and_window_that_are_gone_make_an_orphan(self):
        i = self.run_it(issue("a.cc", 4, lo=2, hi=6), {"a.cc": ["p", "q", "R", "s", "t"]},
                        old={"a.cc": self.WIN_OLD}, u0=self.WIN_U0)
        self.assertTrue(i["orphan"])

    def test_a_closed_issue_stays_an_orphan(self):
        head = {"a.cc": ["a", "b", "c", "d", "x", "y"]}
        for status in CLOSED:
            i = self.run_it(issue("a.cc", 4, status=status), head)
            self.assertEqual(self.where(i), ("a.cc", 4, 4, 4, True, status))

    def test_a_missing_blob_makes_an_orphan(self):
        self.assertTrue(self.run_it(issue("a.cc", 4), {"a.cc": ["x"]}, old={})["orphan"])
        self.assertTrue(self.run_it(issue("a.cc", 4), {})["orphan"])

    def test_a_region_past_the_end_of_the_old_file_makes_an_orphan(self):
        u0 = {"a.cc": {"path": "a.cc", "deleted": False, "hunks": [(6, 10, 5, 0)]}}
        i = self.run_it(issue("a.cc", 6, lo=6, hi=9), {"a.cc": ["d"]}, u0=u0)
        self.assertTrue(i["orphan"])

    def test_a_renamed_file_is_read_at_both_paths(self):
        u0 = {"a.cc": {"path": "b.cc", "deleted": False, "hunks": [(4, 2, 3, 0)]}}
        i = self.run_it(issue("a.cc", 4), {"b.cc": ["a", "b", "c", "d", "x"]}, u0=u0)
        self.assertEqual(self.where(i), ("b.cc", 5, 5, 5, False, "open"))

    def test_only_a_wholly_deleted_open_region_needs_a_search(self):
        u0 = dict(self.U0, **{
            "b.cc": {"path": "c.cc", "deleted": False, "hunks": [(4, 2, 3, 0)]},
            "gone.cc": {"path": "gone.cc", "deleted": True, "hunks": [(1, 9, 0, 0)]}})
        issues = [issue("a.cc", 4), issue("a.cc", 9), issue("a.cc", 4, status="waived"),
                  issue("a.cc", 4, orphan=True), issue("b.cc", 5), issue("gone.cc", 4),
                  issue("other.cc", 4)]
        self.assertEqual(moved_lookups(issues, u0), [("a.cc", "a.cc"), ("b.cc", "c.cc")])


def state_with(*issues):
    return {"v": 3, "sha": "h", "complete": True, "issues": list(issues), "recheck_pending": []}


class WaiverTest(unittest.TestCase):
    def test_applying_a_waiver_twice_is_a_noop(self):
        st = apply_waivers(state_with(issue("a.cc", 3, id="i1")), {"i1"})
        self.assertEqual((st["waived_now"], st["issues"][0]["status"]), (1, "waived"))
        again = apply_waivers(st, {"i1"})
        self.assertEqual(again["waived_now"], 0)
        self.assertEqual(again["issues"], st["issues"])

    def test_a_closed_issue_is_not_changed_by_a_waiver(self):
        st = apply_waivers(state_with(issue("a.cc", 3, id="i1", status="resolved")), {"i1"})
        self.assertEqual((st["issues"][0]["status"], st["waived_now"]), ("resolved", 0))

    def test_an_open_orphan_is_waived_and_unknown_ids_are_ignored(self):
        st = apply_waivers(state_with(issue("a.cc", 3, id="i1", orphan=True)), {"i1", "zz"})
        self.assertEqual((st["issues"][0]["status"], st["waived_now"]), ("waived", 1))

    def test_the_input_state_is_not_modified_and_none_passes(self):
        src = state_with(issue("a.cc", 3, id="i1"))
        apply_waivers(src, {"i1"})
        self.assertEqual(src["issues"][0]["status"], "open")
        self.assertIsNone(apply_waivers(None, {"i1"}))


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

    def test_a_more_severe_finding_near_a_closed_issue_is_new(self):
        f = finding("a.cc", 102, severity="critical", blocking=True, needs_check=False)
        new, dups, _ = classify([f], [issue("a.cc", 100, status="resolved", severity="minor")])
        self.assertEqual((len(new), dups), (1, []))
        self.assertEqual((new[0]["near_closed"], new[0]["blocking"], new[0]["needs_check"]),
                         ("i1", True, False))
        f = finding("a.cc", 102, severity="major", blocking=False, needs_check=False)
        new, _, _ = classify([f], [issue("a.cc", 100, status="resolved", severity="minor")])
        self.assertEqual((new[0]["near_issue"], "near_closed" in new[0]), ("i1", False))

    def test_a_finding_as_severe_near_a_closed_issue_is_kept_and_keeps_its_blocking(self):
        for status in CLOSED:
            for blocking, check in ((True, False), (False, True), (False, False)):
                f = finding("a.cc", 102, blocking=blocking, needs_check=check)
                new, dups, _ = classify([f], [issue("a.cc", 100, status=status)])
                self.assertEqual((len(new), dups), (1, []))
                self.assertEqual((new[0]["near_closed"], new[0]["blocking"],
                                  new[0]["needs_check"]), ("i1", blocking, check))

    def test_a_less_severe_finding_near_a_closed_issue_stays_without_a_check(self):
        f = finding("a.cc", 10, severity="minor", blocking=False, needs_check=False)
        new, _, _ = classify([f], [issue("a.cc", 10, status="waived")])
        self.assertEqual((new[0]["blocking"], new[0]["needs_check"]), (False, False))
        f = finding("a.cc", 10, blocking=False, needs_check=True)
        new, _, _ = classify([f], [issue("a.cc", 10, status="waived")])
        self.assertEqual((new[0]["blocking"], new[0]["needs_check"]), (False, True))

    def test_same_as_an_as_severe_closed_issue_is_dropped_even_as_critical(self):
        for status in CLOSED:
            f = finding("a.cc", 10, severity="critical", same_as="i1")
            new, dups, _ = classify([f], [issue("a.cc", 10, status=status, severity="critical")])
            self.assertEqual((new, [d["dup_of"] for d in dups]), ([], ["i1"]))
            self.assertNotIn("near_closed", dups[0])

    def test_an_open_issue_nearby_wins_over_a_closed_one_when_not_more_severe(self):
        issues = [issue("a.cc", 10, id="c", status="resolved"), issue("a.cc", 12, id="o")]
        new, dups, _ = classify([finding("a.cc", 10)], issues)
        self.assertEqual((new, dups[0]["dup_of"]), ([], "o"))

    def test_a_finding_more_severe_than_every_issue_nearby_is_new(self):
        issues = [issue("a.cc", 10, id="c", status="resolved", severity="minor"),
                  issue("a.cc", 12, id="o", severity="major")]
        f = finding("a.cc", 10, severity="critical", blocking=True, needs_check=False)
        new, dups, _ = classify([f], issues)
        self.assertEqual(([n["near_closed"] for n in new], dups), (["c"], []))
        self.assertEqual((new[0]["blocking"], "near_issue" in new[0]), (True, False))
        f = finding("a.cc", 10, severity="critical", blocking=False, needs_check=True)
        new, dups, _ = classify([f], issues)
        self.assertEqual(([n["near_issue"] for n in new], dups), (["c"], []))

    def test_a_blocking_reraise_next_to_a_waived_issue_without_same_as_is_new(self):
        """Waived critical at 100, open minor at 103, critical re-raises at 101: the accepted
        cost of rev 9 (a non-blocking re-raise still folds)."""
        issues = [issue("a.cc", 100, id="w", status="waived", severity="critical"),
                  issue("a.cc", 103, id="m", severity="minor", blocking=False)]
        f = finding("a.cc", 101, severity="critical", blocking=True, needs_check=False)
        new, dups, _ = classify([f], issues)
        self.assertEqual(([(n["near_closed"], n["blocking"]) for n in new], dups),
                         ([("w", True)], []))
        new, dups, _ = classify([dict(f, blocking=False, needs_check=True)], issues)
        self.assertEqual((new, [d["dup_of"] for d in dups]), ([], ["m"]))
        new, dups, _ = classify([dict(f, same_as="w")], issues)
        self.assertEqual((new, [d["dup_of"] for d in dups]), ([], ["w"]))
    def test_a_more_severe_finding_near_an_open_issue_is_new(self):
        f = finding("a.cc", 12, severity="critical", blocking=True, needs_check=False)
        new, dups, _ = classify([f], [issue("a.cc", 10, severity="minor", blocking=False)])
        self.assertEqual(([n["near_issue"] for n in new], new[0]["blocking"], dups),
                         (["i1"], True, []))

    def test_a_same_severity_reraise_near_an_open_issue_still_merges(self):
        new, dups, _ = classify([finding("a.cc", 12, severity="minor")],
                                [issue("a.cc", 10, severity="minor")])
        self.assertEqual((new, [d["dup_of"] for d in dups]), ([], ["i1"]))

    def test_a_critical_next_to_a_resolved_critical_is_posted_and_blocks(self):
        """Review M2 under rev 9: a resolved critical at 100 and a resolved minor at 103."""
        issues = [issue("a.cc", 100, id="c", status="resolved", severity="critical"),
                  issue("a.cc", 103, id="m", status="resolved", severity="minor")]
        f = finding("a.cc", 101, severity="critical", blocking=True, needs_check=False)
        new, dups, _ = classify([f], issues)
        self.assertEqual(([n["near_closed"] for n in new], new[0]["blocking"],
                          new[0]["needs_check"], dups), (["c"], True, False, []))
        self.assertNotIn("near_issue", new[0])
        # the new issue is open: a blocking re-raise next to it without `same_as` is new
        issues.append(issue("a.cc", 101, id="n", severity="critical"))
        new, dups, _ = classify([finding("a.cc", 102, severity="critical", blocking=True)], issues)
        self.assertEqual(([x["near_issue"] for x in new], dups), (["n"], []))
        new, dups, _ = classify([finding("a.cc", 102, severity="critical")], issues)
        self.assertEqual((new, [d["dup_of"] for d in dups]), ([], ["n"]))


    def test_open_issue_is_preferred_over_closed(self):
        issues = [dict(issue("a.cc", 10, id="c"), status="waived"), issue("a.cc", 12, id="o")]
        self.assertEqual(classify([finding("a.cc", 10)], issues)[1][0]["dup_of"], "o")

    def test_same_as_within_15_lines_links(self):
        new, _, links = classify([finding("a.cc", 25, same_as="i1")], [issue("a.cc", 10, id="i1")])
        self.assertEqual((new, links), ([], [{"id": "i1", "file": "a.cc", "line": 25,
                                                "blocking": False}]))

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
        self.assertEqual((new, links), ([], [{"id": "i1", "file": "b.cc", "line": 500,
                                                "blocking": False}]))

    def test_position_never_matches_an_orphan(self):
        self.assertEqual(len(classify([finding("a.cc", 10)],
                                      [dict(issue("a.cc", 10), orphan=True)])[0]), 1)

    def test_unknown_same_as_is_kept_as_new(self):
        self.assertEqual(len(classify([finding("a.cc", 10, same_as="nope")], [])[0]), 1)

    def test_same_as_does_not_mutate_input(self):
        f = finding("a.cc", 10)
        classify([f], [issue("a.cc", 10)])
        self.assertNotIn("dup_of", f)

    # --- a more severe finding named `same_as` a less severe issue -------------------

    def test_same_as_to_a_less_severe_closed_issue_does_not_drop(self):
        c = issue("a.cc", 100, id="c", status="resolved", severity="minor", blocking=False)
        f = finding("a.cc", 110, severity="critical", blocking=True, needs_check=False,
                    same_as="c")
        new, dups, links = classify([f], [c])
        self.assertEqual(([n.get("via") for n in new], dups, links), (["c"], [], []))
        self.assertTrue(new[0]["blocking"])
        self.assertEqual(new[0]["same_as_ignored"], "c")
        self.assertEqual(new_issue(dict(new[0], id="d"))["via"], "c")

    def test_same_as_to_a_less_severe_open_issue_is_not_a_link(self):
        c = issue("a.cc", 100, id="c", severity="minor", blocking=False)
        f = finding("a.cc", 110, severity="critical", blocking=True, same_as="c")
        new, dups, links = classify([f], [c])
        self.assertEqual(([n.get("via") for n in new], dups, links), (["c"], [], []))
        # near the open issue: new by position (strictly more severe), still in its family
        new, dups, links = classify([dict(f, line=102)], [c])
        self.assertEqual(([(n["via"], n["near_issue"]) for n in new], dups, links),
                         ([("c", "c")], [], []))

    def test_same_as_at_least_as_severe_still_merges(self):
        for sev in ("major", "minor"):
            f = finding("a.cc", 110, severity=sev, same_as="c")
            new, _, links = classify([f], [issue("a.cc", 100, id="c")])
            self.assertEqual((new, [x["id"] for x in links]), ([], ["c"]))
            new, dups, _ = classify([f], [issue("a.cc", 100, id="c", status="resolved")])
            self.assertEqual((new, [d["dup_of"] for d in dups]), ([], ["c"]))
            self.assertNotIn("same_as_ignored", dups[0])

    def test_same_as_naming_an_intermediate_issue(self):
        """Codex chain: minor C 100, major T 110 (via C), critical V 120 (via C), T and V closed."""
        issues = [issue("a.cc", 100, id="c", status="resolved", severity="minor"),
                  issue("a.cc", 110, id="t", status="resolved", severity="major", via="c"),
                  issue("a.cc", 120, id="v", status="resolved", severity="critical", via="c")]
        f = finding("a.cc", 124, severity="critical", blocking=True, same_as="t")
        new, dups, _ = classify([f], issues)
        self.assertEqual((new, [d["dup_of"] for d in dups]), ([], ["v"]))

    def test_same_as_reaches_the_nearest_member_of_the_family(self):
        issues = [issue("a.cc", 100, id="c", status="resolved", severity="minor"),
                  issue("a.cc", 102, id="x", status="resolved", severity="critical", via="c")]
        f = finding("a.cc", 117, severity="critical", same_as="c")  # 17 from c, 15 from x
        new, dups, _ = classify([f], issues)
        self.assertEqual((new, [d["dup_of"] for d in dups]), ([], ["x"]))
        # most severe wins, nearest first on ties: the open x2 over the closed x
        issues.append(issue("a.cc", 118, id="x2", severity="critical", via="c"))
        new, _, links = classify([dict(f, line=122)], issues)
        self.assertEqual((new, [x["id"] for x in links]), ([], ["x2"]))
        # another file's issue with the same source is not in the family
        other = [issue("a.cc", 100, id="c", status="resolved", severity="minor"),
                 issue("b.cc", 120, id="y", status="resolved", severity="critical", via="c")]
        new, _, _ = classify([finding("b.cc", 120, severity="critical", same_as="c")], other)
        self.assertEqual(len(new), 1)

    def drift_runs(self, path, orphan_status="open"):
        """Orphan minor T in a.cc; the model names it for a critical in `path` on five runs,
        each new issue is added to the ledger and the first one is resolved after run 2."""
        issues = [issue("a.cc", 10, id="t", severity="minor", blocking=False, orphan=True,
                        status=orphan_status)]
        out = []
        for n, line in enumerate((50, 54, 58, 62, 66)):
            if n == 2:
                for i in issues:
                    if i.get("via"):
                        i["status"] = "resolved"
            f = finding(path, line, severity="critical", blocking=True, same_as="t")
            new, dups, links = classify([f], issues)
            issues += [new_issue(dict(x, id="x%d" % n)) for x in new]
            out.append((len(new), [d["dup_of"] for d in dups], [x["id"] for x in links]))
        return out, issues

    def test_a_same_as_to_an_orphan_converges_across_files(self):
        for status in ("open", "resolved"):
            for path in ("b.cc", "a.cc"):
                out, issues = self.drift_runs(path, status)
                # run 5 (line 66) is 16 lines from x0: out of reach, so a new issue that
                # joins the family (the accepted drift cost of one issue per SAME_AS lines)
                self.assertEqual(out, [(1, [], []), (0, [], ["x0"]), (0, ["x0"], []),
                                       (0, ["x0"], []), (1, [], [])], (path, status))
                self.assertEqual([(i["id"], i["file"], i.get("via")) for i in issues[1:]],
                                 [("x0", path, "t"), ("x4", path, "t")])

    def test_an_orphan_ties_with_a_family_member_and_takes_the_report(self):
        """The named orphan is as severe as the finding: it wins over the member."""
        for status in ("open", "resolved"):
            issues = [issue("a.cc", 10, id="t", orphan=True),
                      issue("b.cc", 50, id="y", via="t", status=status)]
            new, dups, links = classify([finding("b.cc", 60, same_as="t")], issues)
            self.assertEqual((new, dups, [x["id"] for x in links]), ([], [], ["t"]))

    def test_drift_beyond_the_family_joins_it_only_when_at_least_as_severe(self):
        issues = [issue("a.cc", 100, id="c", status="resolved", severity="minor"),
                  issue("a.cc", 102, id="x", status="resolved", severity="critical", via="c")]
        new, _, _ = classify([finding("a.cc", 118, severity="critical", same_as="c")], issues)
        self.assertEqual([(n.get("via"), "same_as_ignored" in n) for n in new], [("c", False)])
        new, _, _ = classify([finding("a.cc", 300, severity="info", same_as="c")], issues)
        self.assertEqual([n.get("via") for n in new], [None])
        new, _, _ = classify([finding("b.cc", 100, severity="critical", same_as="c")], issues)
        self.assertEqual([n.get("via") for n in new], [None])

    # --- the member a `same_as` merges into is within reach of the finding -----------

    def test_a_same_as_to_an_orphan_relinks_it_past_a_far_more_severe_member(self):
        """Review C1: orphan major T, critical X at 300 (via T); a major at 105 names T."""
        for status in ("open", "resolved"):
            issues = [issue("a.cc", 10, id="t", orphan=True),
                      issue("a.cc", 300, id="x", severity="critical", via="t", status=status)]
            new, dups, links = classify([finding("a.cc", 105, same_as="t")], issues)
            self.assertEqual((new, dups, [x["id"] for x in links]), ([], [], ["t"]), status)

    def test_a_far_member_does_not_swallow_a_more_severe_finding(self):
        """Review I2: resolved minor C at 100, critical X at 300 (via C); a major at 105."""
        for status in ("open", "resolved"):
            issues = [issue("a.cc", 100, id="c", status="resolved", severity="minor"),
                      issue("a.cc", 300, id="x", severity="critical", via="c", status=status)]
            new, dups, links = classify([finding("a.cc", 105, blocking=True, same_as="c")],
                                        issues)
            self.assertEqual(([(n["via"], n["same_as_ignored"]) for n in new], dups, links),
                             ([("c", "c")], [], []), status)
            self.assertTrue(new[0]["blocking"])

    def test_the_named_issue_wins_when_at_least_as_severe(self):
        """T in reach and as severe as the finding: T, not a more severe member nearby."""
        issues = [issue("a.cc", 100, id="t"),
                  issue("a.cc", 104, id="x", severity="critical", via="t")]
        new, _, links = classify([finding("a.cc", 102, same_as="t")], issues)
        self.assertEqual((new, [x["id"] for x in links]), ([], ["t"]))


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


def chunk(*files, index=0):
    return {"index": index, "files": list(files), "diff": "", "est_tokens": 1}


def prev(id_, path, lo, hi, **kw):
    e = issue(path, lo, id=id_, hi=hi, pre="ask", located=True, recheck=True)
    e.update(kw)
    return e


CFG8000 = {"recheck_max_chars": 8000}


class AssignRecheckTest(unittest.TestCase):
    def assign(self, chunks, previous, blobs, cfg=CFG8000):
        from review.ledger import assign_recheck
        return assign_recheck(chunks, previous, blobs, cfg)

    def test_recheck_context_has_20_lines_each_side(self):
        by, left = self.assign([chunk("a.cc")], [prev("i1", "a.cc", 50, 50)],
                               {"a.cc": ["x"] * 100})
        self.assertEqual((by[0][0]["start"], by[0][0]["text"].count("\n") + 1, left),
                         (30, 41, []))
        self.assertEqual((by[0][0]["id"], by[0][0]["file"], by[0][0]["lo"], by[0][0]["hi"]),
                         ("i1", "a.cc", 30, 70))

    def test_context_is_clipped_to_the_file_and_the_region_is_kept(self):
        by, _ = self.assign([chunk("a.cc")], [prev("i1", "a.cc", 3, 8)],
                            {"a.cc": ["l%d" % n for n in range(1, 31)]})
        item = by[0][0]
        self.assertEqual((item["start"], item["hi"]), (1, 28))
        self.assertEqual(item["text"].split("\n")[:2], ["l1", "l2"])
        self.assertFalse(item["text"].endswith("\n"))

    def test_a_first_item_over_budget_is_served_with_less_context(self):
        lines = ["%03d" % n + "x" * 497 for n in range(1, 101)]
        by, left = self.assign([chunk("a.cc")], [prev("i1", "a.cc", 50, 50)], {"a.cc": lines})
        item = by[0][0]
        self.assertEqual((item["lo"], item["hi"], item["start"], left), (43, 57, 43, []))
        self.assertEqual(item["text"], "\n".join(lines[42:57]))
        self.assertLessEqual(len(item["text"]), 8000)

    def test_a_region_alone_over_budget_is_served_alone(self):
        lines = ["x" * 500] * 100
        by, left = self.assign([chunk("a.cc")], [prev("i1", "a.cc", 50, 70),
                                                 prev("i2", "a.cc", 10, 10)], {"a.cc": lines})
        item = by[0][0]
        self.assertEqual((item["lo"], item["hi"], item["start"], len(by[0]), left),
                         (50, 70, 50, 1, ["i2"]))
        self.assertEqual(item["text"].count("\n") + 1, 21)

    def test_a_very_long_line_is_cut_so_the_item_and_prompt_stay_bounded(self):
        from review.prompt import build_prompt
        lines = ["int a%d;" % n for n in range(1, 101)]
        lines[49] = "int huge = " + "1+" * 150000 + "1;"  # a 300 KB line
        by, left = self.assign([chunk("a.cc")], [prev("i1", "a.cc", 50, 50)], {"a.cc": lines})
        item = by[0][0]
        rows = item["text"].split("\n")
        self.assertEqual((item["lo"], item["hi"], item["start"], len(rows), left),
                         (30, 70, 30, 41, []))
        self.assertEqual(rows[20], lines[49][:4000] + " …(줄 잘림)")
        self.assertEqual(rows[19], lines[48])
        self.assertEqual((item["cut"], item["region_cut"]), ([50], True))
        ctx = {"chunk_total": 1, "mode": "incremental", "range": "a..b", "pr_title": "t",
               "previous": [], "explore_budget": 5, "recheck": by}
        p = build_prompt(dict(chunk("a.cc"), diff="+x\n"), ctx)
        self.assertIn("50: " + lines[49][:4000] + " …(줄 잘림)", p)
        self.assertLess(len(p), 20000)

    def test_a_region_line_is_cut_at_4000_and_a_context_line_at_500(self):
        lines = ["int a%d;" % n for n in range(1, 101)]
        lines[48] = "c" * 1000  # context
        lines[49] = "r" * 3000  # region, shown whole
        lines[50] = "s" * 6000  # region, cut
        by, _ = self.assign([chunk("a.cc")], [prev("i1", "a.cc", 50, 51)], {"a.cc": lines})
        item = by[0][0]
        rows = item["text"].split("\n")
        self.assertEqual(item["start"], 30)
        self.assertEqual(rows[19], "c" * 500 + " …(줄 잘림)")
        self.assertEqual(rows[20], "r" * 3000)
        self.assertEqual(rows[21], "s" * 4000 + " …(줄 잘림)")
        self.assertEqual((item["cut"], item["region_cut"]), ([49, 51], True))

    def test_an_item_without_a_cut_region_line(self):
        lines = ["int a%d;" % n for n in range(1, 101)]
        lines[48] = "c" * 1000
        by, _ = self.assign([chunk("a.cc")], [prev("i1", "a.cc", 50, 50)], {"a.cc": lines})
        self.assertEqual((by[0][0]["cut"], by[0][0]["region_cut"]), ([49], False))

    def test_an_item_over_the_rest_of_a_used_budget_is_left_out(self):
        lines = ["x" * 100] * 100
        by, left = self.assign([chunk("a.cc")], [prev("i1", "a.cc", 50, 50),
                                                 prev("i2", "a.cc", 20, 20)], {"a.cc": lines},
                               {"recheck_max_chars": 5000})
        self.assertEqual(([i["id"] for i in by[0]], left), (["i1"], ["i2"]))

    def test_budget_is_per_chunk_and_shared_by_its_items(self):
        blobs = {"a.cc": ["x" * 100] * 100}
        cfg = {"recheck_max_chars": 5000}
        by, left = self.assign([chunk("a.cc")], [prev("i1", "a.cc", 50, 50),
                                                 prev("i2", "a.cc", 50, 50)], blobs, cfg)
        self.assertEqual(([i["id"] for i in by[0]], left), (["i1"], ["i2"]))

    def test_item_goes_in_the_first_chunk_that_holds_the_file(self):
        by, left = self.assign([chunk("b.cc", index=0), chunk("a.cc", "b.cc", index=1),
                                chunk("a.cc", index=2)], [prev("i1", "a.cc", 5, 5)],
                               {"a.cc": ["x"] * 10})
        self.assertEqual(({k: [i["id"] for i in v] for k, v in by.items()}, left),
                         ({1: ["i1"]}, []))

    def test_a_file_in_no_chunk_is_left_out(self):
        by, left = self.assign([chunk("b.cc")], [prev("i1", "a.cc", 5, 5)], {"a.cc": ["x"] * 10})
        self.assertEqual((by, left), ({}, ["i1"]))

    def test_only_issues_marked_for_recheck_are_items(self):
        previous = [prev("o1", "a.cc", 5, 5, orphan=True, pre="orphan"),
                    prev("r1", "a.cc", 5, 5, pre="resolved"),
                    prev("n1", "a.cc", 5, 5, recheck=False),
                    prev("g1", "gone.cc", 5, 5)]
        by, left = self.assign([chunk("a.cc", "gone.cc")], previous, {"a.cc": ["x"] * 10})
        self.assertEqual((by, left), ({}, []))  # gone.cc has no blob: nothing to show


    def test_a_pending_id_gets_the_slot_before_the_others(self):
        blobs = {"a.cc": ["x" * 100] * 100}
        cfg = {"recheck_max_chars": 5000}  # room for one item
        previous = [prev("i1", "a.cc", 50, 50), prev("i2", "a.cc", 50, 50, pending=True)]
        by, left = self.assign([chunk("a.cc")], previous, blobs, cfg)
        self.assertEqual(([i["id"] for i in by[0]], left), (["i2"], ["i1"]))

    def test_the_order_of_the_rest_stays_stable(self):
        blobs = {"a.cc": ["x" * 100] * 100}
        cfg = {"recheck_max_chars": 9000}  # room for two items
        previous = [prev("i1", "a.cc", 50, 50), prev("i2", "a.cc", 50, 50),
                    prev("i3", "a.cc", 50, 50, pending=True), prev("i4", "a.cc", 50, 50)]
        by, left = self.assign([chunk("a.cc")], previous, blobs, cfg)
        self.assertEqual(([i["id"] for i in by[0]], left), (["i3", "i1"], ["i2", "i4"]))


class PendingAfterTest(unittest.TestCase):
    def test_recheck_in_a_failed_chunk_is_pending(self):
        from review.ledger import pending_after
        self.assertEqual(pending_after({0: [{"id": "i1"}]}, ["i2"],
                                       [{"index": 0, "status": "failed"}], []), ["i2", "i1"])

    def test_recheck_in_an_ok_chunk_is_not_pending(self):
        from review.ledger import pending_after
        results = [{"index": 0, "status": "ok"}, {"index": 1, "status": "partial"}]
        self.assertEqual(pending_after({0: [{"id": "a"}], 1: [{"id": "b"}], 2: [{"id": "c"}]},
                                       [], results, []), ["b", "c"])

    def test_recheck_in_an_ok_chunk_is_pending_when_another_chunk_of_its_file_is_not_ok(self):
        from review.ledger import pending_after
        chunks = [{"index": 0, "files": ["a.cc", "b.cc"]}, {"index": 1, "files": ["a.cc"]},
                  {"index": 2, "files": ["c.cc"]}]
        results = [{"index": 0, "status": "ok"}, {"index": 1, "status": "failed"},
                   {"index": 2, "status": "ok"}]
        recheck = {0: [{"id": "a", "file": "a.cc"}, {"id": "b", "file": "b.cc"}],
                   2: [{"id": "c", "file": "c.cc"}]}
        self.assertEqual(pending_after(recheck, [], results, chunks), ["a"])

    def test_an_id_is_listed_once(self):
        from review.ledger import pending_after
        chunks = [{"index": 0, "files": ["a.cc"]}, {"index": 1, "files": ["a.cc"]}]
        results = [{"index": 0, "status": "partial"}, {"index": 1, "status": "failed"}]
        self.assertEqual(pending_after({0: [{"id": "a", "file": "a.cc"}]}, ["a"], results, chunks),
                         ["a"])

    def test_a_chunk_without_a_result_is_not_ok(self):
        from review.ledger import pending_after
        chunks = [{"index": 0, "files": ["a.cc"]}, {"index": 1, "files": ["a.cc"]}]
        self.assertEqual(pending_after({0: [{"id": "a", "file": "a.cc"}]}, [],
                                       [{"index": 0, "status": "ok"}], chunks), ["a"])


    def test_a_recheck_of_a_skipped_or_truncated_file_is_pending(self):
        from review.ledger import pending_after
        chunks = [{"index": 0, "files": ["a.cc", "b.cc", "c.cc"]}]
        recheck = {0: [{"id": "a", "file": "a.cc"}, {"id": "b", "file": "b.cc"},
                       {"id": "c", "file": "c.cc"}]}
        self.assertEqual(pending_after(recheck, ["z"], [{"index": 0, "status": "ok"}], chunks,
                                       skipped=["a.cc"], truncated=["c.cc"]), ["z", "a", "c"])

    def test_a_recheck_of_a_recheck_only_file_follows_its_chunks_only(self):
        from review.ledger import pending_after
        chunks = [{"index": 0, "files": ["a.cc", "b.cc"]}, {"index": 1, "files": ["b.cc"]}]
        recheck = {0: [{"id": "a", "file": "a.cc"}, {"id": "b", "file": "b.cc"}]}
        results = [{"index": 0, "status": "ok"}, {"index": 1, "status": "failed"}]
        self.assertEqual(pending_after(recheck, ["z"], results, chunks, skipped=["a.cc", "b.cc"],
                                       truncated=["a.cc"], recheck_only={"a.cc", "b.cc"}),
                         ["z", "b"])

    def test_recheck_only_does_not_cover_other_files(self):
        from review.ledger import pending_after
        chunks = [{"index": 0, "files": ["a.cc", "c.cc"]}]
        recheck = {0: [{"id": "a", "file": "a.cc"}, {"id": "c", "file": "c.cc"}]}
        self.assertEqual(pending_after(recheck, [], [{"index": 0, "status": "ok"}], chunks,
                                       skipped=["a.cc", "c.cc"], recheck_only={"a.cc"}), ["c"])


class PromptListsTest(unittest.TestCase):
    def test_open_and_closed_issues_go_to_the_ledger_and_orphans_to_the_orphan_list(self):
        from review.ledger import prompt_lists
        previous = [issue("a.cc", 5, id="o1", pre="open"), issue("a.cc", 6, id="o2", pre="ask"),
                    issue("a.cc", 7, id="r1", pre="resolved"),
                    issue("a.cc", 8, id="p1", pre="orphan", orphan=True)]
        closed = [issue("a.cc", 9, id="c1", status="waived"),
                  issue("gone.cc", 9, id="c2", status="resolved", orphan=True)]
        ledger, orphans = prompt_lists(previous, closed)
        self.assertEqual(([i["id"] for i in ledger], [i["id"] for i in orphans]),
                         (["o1", "o2", "c1"], ["p1", "c2"]))


if __name__ == "__main__":
    unittest.main()


class ShownLinesTest(unittest.TestCase):
    DIFF = ("diff --git a/a.cc b/a.cc\n--- a/a.cc\n+++ b/a.cc\n@@ -1,2 +1,3 @@\n x\n+y\n z\n")

    def test_right_lines_of_each_chunk(self):
        from review.ledger import shown_lines
        out = shown_lines([{"index": 0, "diff": self.DIFF}, {"index": 1, "diff": ""}], {})
        self.assertEqual(out, {0: {"a.cc": {1, 2, 3}}, 1: {}})

    def test_recheck_lines_are_numbered_from_start(self):
        from review.ledger import shown_lines
        item = {"id": "i1", "file": "a.cc", "lo": 40, "hi": 42, "start": 40, "text": "p\nq\nr"}
        out = shown_lines([{"index": 0, "diff": self.DIFF}], {0: [item]})
        self.assertEqual(out[0]["a.cc"], {1, 2, 3, 40, 41, 42})

    def test_cut_recheck_lines_are_not_shown(self):
        from review.ledger import shown_lines
        item = {"id": "i1", "file": "a.cc", "lo": 40, "hi": 42, "start": 40, "text": "p\nq\nr",
                "cut": [41]}
        out = shown_lines([{"index": 0, "diff": self.DIFF}], {0: [item]})
        self.assertEqual(out[0]["a.cc"], {1, 2, 3, 40, 42})

    def test_a_cut_line_shown_whole_by_the_diff_counts(self):
        from review.ledger import shown_lines
        item = {"id": "i1", "file": "a.cc", "lo": 1, "hi": 3, "start": 1, "text": "x\ny\nz",
                "cut": [2]}
        out = shown_lines([{"index": 0, "diff": self.DIFF}], {0: [item]})
        self.assertEqual(out[0]["a.cc"], {1, 2, 3})

    def test_a_row_cut_in_the_chunk_diff_is_not_shown(self):
        from review.ledger import shown_lines
        out = shown_lines([{"index": 0, "diff": self.DIFF, "cut": {"a.cc": [2]}}], {})
        self.assertEqual(out[0]["a.cc"], {1, 3})

    def test_a_row_cut_in_the_diff_shown_whole_by_a_recheck_counts(self):
        from review.ledger import shown_lines
        item = {"id": "i1", "file": "a.cc", "lo": 1, "hi": 3, "start": 1, "text": "x\ny\nz"}
        out = shown_lines([{"index": 0, "diff": self.DIFF, "cut": {"a.cc": [2]}}], {0: [item]})
        self.assertEqual(out[0]["a.cc"], {1, 2, 3})

    def test_an_issue_on_a_cut_row_is_not_resolved(self):
        from review.aggregate import merge
        from review.config import load_config
        from review.ledger import shown_lines
        chunks = [{"index": 0, "files": ["a.cc"], "diff": self.DIFF, "cut": {"a.cc": [2]}}]
        prev = [dict(issue("a.cc", 2, id="i1"), pre="open", located=True),
                dict(issue("a.cc", 3, id="i3", lo=3, hi=3), pre="open", located=True)]
        out = {"summary": "s", "findings": [], "incomplete_reason": None, "diff_reviewed": True,
               "previous_findings": [{"id": "i1", "status": "resolved"},
                                     {"id": "i3", "status": "resolved"}]}
        review = merge([{"index": 0, "status": "ok", "output": out}], load_config(), prev,
                       {"a.cc": {2}}, False, chunk_files={0: ["a.cc"]},
                       shown=shown_lines(chunks, {}))
        self.assertEqual({p["id"]: p["status"] for p in review["previous"]},
                         {"i1": "open", "i3": "resolved"})


class FinishOrphansTest(unittest.TestCase):
    def test_only_open_orphans_change(self):
        from review.ledger import finish_orphans
        a = dict(issue("a.cc", 5, id="o", pre="orphan", orphan=True))
        b = dict(issue("a.cc", 6, id="n"))
        finish_orphans([a, b], [{"id": "n", "file": "z", "line": 1}], True)
        self.assertEqual((a["status"], b["status"], b["file"]), ("resolved", "open", "a.cc"))


class PositionOnlyAnnotatesBlockingTest(unittest.TestCase):
    """Rev 9: position never merges, swallows or un-blocks a blocking finding."""

    def blocking(self, line, **kw):
        return finding("a.cc", line, blocking=True, needs_check=False, **kw)

    def test_a_blocking_finding_near_an_open_issue_is_new(self):
        """Codex #1: open major A at 50, a distinct major/95 B at 52 without `same_as`."""
        new, dups, links = classify([self.blocking(52, confidence=95)],
                                    [issue("a.cc", 50, id="A")])
        self.assertEqual((dups, links), ([], []))
        self.assertEqual([(n["near_issue"], n["blocking"], n["needs_check"], "near_closed" in n)
                          for n in new], [("A", True, False, False)])

    def test_a_blocking_finding_near_a_closed_issue_keeps_blocking(self):
        """Codex #4: resolved critical at 100, a new major/95 at 102."""
        for status in CLOSED:
            new, dups, _ = classify([self.blocking(102, confidence=95)],
                                    [issue("a.cc", 100, status=status, severity="critical")])
            self.assertEqual(dups, [])
            self.assertEqual([(n["near_closed"], n["blocking"], n["needs_check"],
                               "near_issue" in n) for n in new], [("i1", True, False, False)])

    def test_the_nearest_issue_names_the_note(self):
        issues = [issue("a.cc", 100, id="c", status="resolved", severity="critical"),
                  issue("a.cc", 103, id="o")]
        new, _, _ = classify([self.blocking(102)], issues)
        self.assertEqual((new[0]["near_issue"], "near_closed" in new[0]), ("o", False))
        new, _, _ = classify([self.blocking(101)], issues)
        self.assertEqual((new[0]["near_closed"], "near_issue" in new[0]), ("c", False))

    def test_a_blocking_finding_named_same_as_an_open_issue_still_links(self):
        new, dups, links = classify([self.blocking(52, same_as="A")],
                                    [issue("a.cc", 50, id="A")])
        self.assertEqual((new, dups, [x["id"] for x in links]), ([], [], ["A"]))

    def test_a_non_blocking_finding_still_folds_and_notes_a_closed_issue(self):
        f = finding("a.cc", 52, severity="minor", blocking=False, needs_check=False)
        new, dups, _ = classify([f], [issue("a.cc", 50, id="A", severity="minor")])
        self.assertEqual((new, [(d["dup_of"], d["folded"]) for d in dups]), ([], [("A", True)]))
        new, dups, _ = classify([f], [issue("a.cc", 50, id="A", severity="minor",
                                            status="resolved")])
        self.assertEqual((dups, [(n["near_closed"], n["blocking"]) for n in new]),
                         ([], [("A", False)]))

    def test_links_carry_the_blocking_of_their_finding(self):
        _, _, links = classify([self.blocking(52, same_as="A"),
                                finding("a.cc", 53, severity="minor", same_as="A")],
                               [issue("a.cc", 50, id="A")])
        self.assertEqual([x["blocking"] for x in links], [True, False])


class ExactReReportTest(unittest.TestCase):
    """An exact re-report (same file, line and title, not more severe) of an open issue links."""

    def test_an_exact_re_report_of_an_open_issue_links(self):
        for blocking in (True, False):
            f = finding("a.cc", 10, title="t", blocking=blocking)
            new, dups, links = classify([f], [issue("a.cc", 10)])
            self.assertEqual((new, dups, [(x["id"], x["blocking"]) for x in links]),
                             ([], [], [("i1", blocking)]))
        f = finding("a.cc", 10, title="t", severity="minor", blocking=False)
        self.assertEqual([x["id"] for x in classify([f], [issue("a.cc", 10)])[2]], ["i1"])

    def test_other_title_more_severe_closed_or_orphan_do_not_link(self):
        cases = [(finding("a.cc", 10, title="u", blocking=True), issue("a.cc", 10)),
                 (finding("a.cc", 10, title="t", severity="critical", blocking=True),
                  issue("a.cc", 10)),
                 (finding("a.cc", 10, title="t", blocking=True),
                  issue("a.cc", 10, status="resolved")),
                 (finding("a.cc", 10, title="t", blocking=True),
                  issue("a.cc", 10, orphan=True))]
        for f, i in cases:
            new, dups, links = classify([f], [i])
            self.assertEqual((len(new), dups, links), (1, [], []))
