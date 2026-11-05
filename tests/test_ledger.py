import unittest

from review.fingerprint import fp_line
from review.ledger import CLOSED, advance, apply_waivers, classify, finding_id, from_v2, new_issue, unique_id

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

    def test_recheck_over_budget_is_left_out(self):
        by, left = self.assign([chunk("a.cc")], [prev("i1", "a.cc", 50, 50)],
                               {"a.cc": ["x" * 500] * 100})
        self.assertEqual((by, left), ({}, ["i1"]))

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
                                       [{"index": 0, "status": "failed"}]), ["i2", "i1"])

    def test_recheck_in_an_ok_chunk_is_not_pending(self):
        from review.ledger import pending_after
        results = [{"index": 0, "status": "ok"}, {"index": 1, "status": "partial"}]
        self.assertEqual(pending_after({0: [{"id": "a"}], 1: [{"id": "b"}], 2: [{"id": "c"}]},
                                       [], results), ["b", "c"])


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


class FinishOrphansTest(unittest.TestCase):
    def test_only_open_orphans_change(self):
        from review.ledger import finish_orphans
        a = dict(issue("a.cc", 5, id="o", pre="orphan", orphan=True))
        b = dict(issue("a.cc", 6, id="n"))
        finish_orphans([a, b], [{"id": "n", "file": "z", "line": 1}], True)
        self.assertEqual((a["status"], b["status"], b["file"]), ("resolved", "open", "a.cc"))
