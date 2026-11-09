import unittest

from review.aggregate import merge
from review.render import render_body
from tests.test_aggregate import CFG, F, P, res
from tests.test_render import META


def run(results, prev=(), chunk_files=None, truncated=None, anchor=None, closed=None):
    return merge(results, CFG, list(prev), {"a.cu": {10}}, False, anchor=anchor,
                 chunk_files=chunk_files, truncated=truncated, closed=closed)


class ResolvedTrustTest(unittest.TestCase):
    def test_resolved_from_chunk_with_note_accepted(self):
        r = run([res(0, [], reason="x", prev=[("f1", "resolved")])],
                [P(blocking=True)], {0: ["a.cu"]})
        self.assertEqual(r["previous"][0]["status"], "resolved")

    def test_resolved_from_partial_chunk_ignored(self):
        r = run([res(0, [], status="partial", prev=[("f1", "resolved")])],
                [P(blocking=True)], {0: ["a.cu"]})
        self.assertEqual((r["previous"][0]["status"], r["event"]), ("open", "REQUEST_CHANGES"))

    def test_open_from_partial_chunk_accepted(self):
        r = run([res(0, [], status="partial", prev=[("f1", "unclear")])],
                [P(blocking=True)], {0: ["a.cu"]})
        self.assertEqual(r["previous"][0]["status"], "unclear")

    def test_resolved_from_incomplete_chunk_ignored(self):
        r = run([res(0, [], status="incomplete", prev=[("f1", "resolved")])],
                [P(blocking=True)], {0: ["a.cu"]})
        self.assertEqual((r["previous"][0]["status"], r["event"]), ("open", "REQUEST_CHANGES"))

    def test_resolved_ignored_when_other_chunk_of_file_failed(self):
        results = [res(0, [], prev=[("f1", "resolved")]), res(1, [], status="failed")]
        r = run(results, [P(blocking=True)], {0: ["a.cu"], 1: ["a.cu"]})
        self.assertEqual(r["previous"][0]["status"], "open")

    def test_resolved_kept_when_failed_chunk_has_other_files(self):
        results = [res(0, [], prev=[("f1", "resolved")]), res(1, [], status="failed")]
        r = run(results, [P(blocking=True)], {0: ["a.cu"], 1: ["b.cu"]})
        self.assertEqual((r["previous"][0]["status"], r["event"]), ("resolved", "COMMENT"))

    def test_resolved_without_chunk_map_needs_all_ok(self):
        results = [res(0, [], prev=[("f1", "resolved")]), res(1, [], status="failed")]
        self.assertEqual(run(results, [P(blocking=True)])["previous"][0]["status"], "open")
        r = run([res(0, [], prev=[("f1", "resolved")])], [P(blocking=True)])
        self.assertEqual(r["previous"][0]["status"], "resolved")

    def test_resolved_from_chunk_without_the_file_ignored(self):
        r = run([res(0, [], prev=[("f1", "resolved")])], [P(blocking=True)], {0: ["b.cu"]})
        self.assertEqual(r["previous"][0]["status"], "open")


class RereportAndTruncTest(unittest.TestCase):
    def prev(self, blocking):
        return dict(P(id="p1", blocking=blocking), anchor="aaaa0000", pre="ask", located=True)

    def test_a_more_severe_finding_near_an_open_issue_is_new(self):
        r = run([res(0, [F(title="other", sev="critical", conf=95)])], [self.prev(False)],
                anchor=lambda p, l: "aaaa0000")
        self.assertEqual(([f["near_issue"] for f in r["findings"]], r["duplicates"]), (["p1"], []))
        self.assertFalse(r["previous"][0]["blocking"])  # the issue keeps its severity
        self.assertEqual(r["event"], "REQUEST_CHANGES")

    def test_a_critical_next_to_an_open_minor_reported_resolved_blocks(self):
        """Review I1: the issue closes in the same run that reports the new defect."""
        prev = dict(P(id="p1", line=10, sev="minor"), pre="ask", located=True, lo=10, hi=10)
        r = merge([res(0, [F(line=12, sev="critical", conf=95, title="new")],
                       prev=[("p1", "resolved")])],
                  CFG, [prev], {"a.cu": {12}}, False, chunk_files={0: ["a.cu"]})
        self.assertEqual(([f["near_issue"] for f in r["findings"]], r["duplicates"]), (["p1"], []))
        self.assertTrue(r["findings"][0]["blocking"])
        self.assertEqual((r["previous"][0]["status"], r["event"]), ("resolved", "REQUEST_CHANGES"))

    def test_not_more_severe_rereport_dropped_unless_blocking(self):
        r = run([res(0, [F(title="other", sev="minor", conf=95)])], [self.prev(False)],
                anchor=lambda p, l: "aaaa0000")
        self.assertEqual(r["findings"], [])
        r = run([res(0, [F(title="other", sev="major", conf=95)])], [self.prev(True)],
                anchor=lambda p, l: "aaaa0000")
        self.assertEqual([(f["near_issue"], f["blocking"]) for f in r["findings"]],
                         [("p1", True)])

    def test_new_ids_never_collide_with_known_ids(self):
        from review.ledger import finding_id
        taken = finding_id(F(line=10, title="t"))
        closed = [dict(P(id=taken, line=300), status="resolved")]
        r = run([res(0, [F(line=10)])], [], closed=closed)
        self.assertEqual(len(r["findings"]), 1)
        self.assertNotEqual(r["findings"][0]["id"], taken)

    def test_closed_issue_swallows_a_reraise_named_by_same_as(self):
        closed = [dict(P(id="c1", sev="critical"), status="resolved")]
        f = dict(F(sev="critical", conf=95), same_as="c1")
        r = run([res(0, [f])], [], closed=closed)
        self.assertEqual((r["findings"], len(r["duplicates"]), r["event"]), ([], 1, "APPROVE"))

    def test_a_same_as_to_a_less_severe_closed_issue_is_new_and_blocks(self):
        closed = [dict(P(id="c1", sev="minor"), status="resolved")]
        f = dict(F(sev="critical", conf=95), same_as="c1")
        r = run([res(0, [f])], [], closed=closed)
        self.assertEqual(([x["via"] for x in r["findings"]], r["duplicates"], r["event"]),
                         (["c1"], [], "REQUEST_CHANGES"))

    def test_a_more_severe_defect_near_a_resolved_issue_blocks(self):
        closed = [dict(P(id="c1", line=100, sev="minor"), status="resolved")]
        r = merge([res(0, [F(line=102, sev="critical", conf=95)])], CFG, [],
                  {"a.cu": {102}}, False, closed=closed)
        self.assertEqual((len(r["findings"]), r["duplicates"], r["event"]),
                         (1, [], "REQUEST_CHANGES"))
        self.assertEqual((r["findings"][0]["near_closed"], r["findings"][0]["blocking"]),
                         ("c1", True))

    def test_an_as_severe_defect_near_a_resolved_issue_is_posted_and_blocks(self):
        for status in ("resolved", "waived"):
            closed = [dict(P(id="c1", line=100), status=status)]
            r = merge([res(0, [F(line=102, conf=95)])], CFG, [], {"a.cu": {102}}, False,
                      closed=closed)
            f = r["findings"][0]
            self.assertEqual((f["near_closed"], f["blocking"], f["needs_check"], r["duplicates"],
                              r["event"]), ("c1", True, False, [], "REQUEST_CHANGES"))

    def test_truncated_makes_incomplete_comment(self):
        r = run([res(0, [])], truncated=["big.py"])
        self.assertEqual((r["event"], r["complete"], r["truncated"]), ("COMMENT", False, ["big.py"]))
        self.assertTrue(run([res(0, [])])["complete"])


class ReporterShownTest(unittest.TestCase):
    """With `shown`, only a chunk that showed the issue's region can report on the issue."""

    def two_chunks(self, first, second):
        results = [res(0, [], prev=[("i1", first)]), res(1, [], prev=[("i1", second)])]
        return run_merge(results, issue("a.cu", 50), chunk_files={0: ["a.cu"], 1: ["a.cu"]},
                         shown={0: {"a.cu": {1, 2}}, 1: {"a.cu": {49, 50, 51}}})

    def test_resolved_from_a_chunk_that_did_not_show_the_region_is_ignored(self):
        self.assertEqual(status(self.two_chunks("resolved", "open"), "i1"), "open")
        out = run_merge([res(0, [], prev=[("i1", "resolved")]), res(1, [])], issue("a.cu", 50),
                        chunk_files={0: ["a.cu"], 1: ["a.cu"]},
                        shown={0: {"a.cu": {1, 2}}, 1: {"a.cu": {49, 50, 51}}})
        self.assertEqual(status(out, "i1"), "open")

    def test_open_from_a_chunk_that_did_not_show_the_region_is_ignored(self):
        self.assertEqual(status(self.two_chunks("open", "resolved"), "i1"), "resolved")

    def test_unclear_from_a_chunk_that_did_not_show_the_region_is_ignored(self):
        out = run_merge([res(0, [], prev=[("i1", "unclear")]), res(1, [])], issue("a.cu", 50),
                        chunk_files={0: ["a.cu"], 1: ["a.cu"]},
                        shown={0: {"a.cu": {1, 2}}, 1: {"a.cu": {50}}})
        self.assertEqual(status(out, "i1"), "open")

    def test_open_from_a_chunk_that_showed_the_region_is_kept(self):
        out = run_merge([res(0, [], prev=[("i1", "unclear")])], issue("a.cu", 50),
                        shown={0: {"a.cu": {50}}})
        self.assertEqual(status(out, "i1"), "unclear")


if __name__ == "__main__":
    unittest.main()


def issue(path, line, **kw):
    base = dict(P(id="i1", file=path, line=line, blocking=True), lo=line, hi=line, orphan=False)
    base.update(kw)
    return base


def orphan_prev(id_):
    return issue("a.cu", 10, id=id_, orphan=True, pre="orphan")


def run_merge(results, prev, shown=None, skipped=None, truncated=None, chunk_files=None):
    changed = {"a.cu": {10, 50}, "b.cc": {500}}
    return merge(results, CFG, [prev], changed, False, skipped=skipped, truncated=truncated,
                 chunk_files=chunk_files, shown=shown)


def resolved_report(id_):
    return [res(0, [], prev=[(id_, "resolved")])]


def link_report(id_, path, line):
    return [res(0, [dict(F(file=path, line=line), same_as=id_)])]


def failed_chunk():
    return [res(0, [], status="failed")]


all_failed = failed_chunk


def entry(out, id_):
    return next(p for p in out["previous"] if p["id"] == id_)


def status(out, id_):
    return entry(out, id_)["status"]


def run_two_chunks(ok_status, other_status):
    results = [res(0, [], status=ok_status, prev=[("i1", "resolved")]),
               res(1, [], status=other_status)]
    return run_merge(results, issue("a.cu", 50), chunk_files={0: ["a.cu"], 1: ["a.cu"]},
                     shown={0: {"a.cu": {50}}, 1: {"a.cu": {50}}})


class ClosureRulesTest(unittest.TestCase):
    def test_resolved_needs_the_region_in_shown_lines(self):
        out = run_merge(resolved_report("i1"), issue("a.cu", 50, id="i1"),
                        shown={0: {"a.cu": {1, 2, 3}}})
        self.assertEqual(status(out, "i1"), "open")

    def test_resolved_is_accepted_when_the_region_was_shown(self):
        out = run_merge(resolved_report("i1"), issue("a.cu", 50, id="i1"),
                        shown={0: {"a.cu": {49, 50, 51}}})
        self.assertEqual(status(out, "i1"), "resolved")

    def test_resolved_needs_the_whole_region(self):
        out = run_merge(resolved_report("i1"), issue("a.cu", 50, id="i1", lo=48, hi=52),
                        shown={0: {"a.cu": {49, 50, 51}}})
        self.assertEqual(status(out, "i1"), "open")

    def test_resolved_is_rejected_for_a_skipped_file(self):
        out = run_merge(resolved_report("i1"), issue("a.cu", 50, id="i1"), skipped=["a.cu"],
                        shown={0: {"a.cu": {50}}})
        self.assertEqual(status(out, "i1"), "open")

    def test_resolved_is_rejected_for_a_truncated_file(self):
        out = run_merge(resolved_report("i1"), issue("a.cu", 50, id="i1"), truncated=["a.cu"],
                        shown={0: {"a.cu": {50}}})
        self.assertEqual(status(out, "i1"), "open")

    def test_resolved_needs_every_chunk_holding_the_file_ok(self):
        out = run_two_chunks(ok_status="ok", other_status="partial")
        self.assertEqual(status(out, "i1"), "open")

    def test_open_orphan_resolves_on_a_complete_run(self):
        self.assertEqual(status(run_merge([], orphan_prev("i1")), "i1"), "resolved")

    def test_open_orphan_stays_open_on_an_incomplete_run(self):
        self.assertEqual(status(run_merge(failed_chunk(), orphan_prev("i1")), "i1"), "open")

    def test_linked_orphan_stays_open_at_the_finding(self):
        out = run_merge(link_report("i1", "b.cc", 500), orphan_prev("i1"))
        p = entry(out, "i1")
        self.assertEqual((p["status"], p["file"], p["line"], p.get("orphan")),
                         ("open", "b.cc", 500, False))

    def test_link_to_a_non_orphan_changes_nothing(self):
        out = run_merge(link_report("i1", "a.cu", 55), issue("a.cu", 50))
        p = entry(out, "i1")
        self.assertEqual((p["file"], p["line"], p["status"]), ("a.cu", 50, "open"))

    def test_all_chunks_failed_closes_nothing(self):
        out = run_merge(all_failed(), issue("a.cu", 50, id="i1"), shown={})
        self.assertEqual(status(out, "i1"), "open")
        self.assertEqual(out["event"], "REQUEST_CHANGES")

    def test_resolved_orphan_does_not_block(self):
        out = run_merge([], orphan_prev("i1"))
        self.assertEqual(out["event"], "APPROVE")


def run_recheck_only(results, prev, chunk_files, shown, skipped=None, truncated=None,
                     recheck_only=None, left_out=None):
    return merge(results, CFG, [prev], {"b.cc": {500}}, False, skipped=skipped,
                 truncated=truncated, chunk_files=chunk_files, shown=shown,
                 recheck_only=recheck_only, left_out=left_out)


class RecheckOnlyTest(unittest.TestCase):
    """A file that is a target only for rechecks (no line of it is in `changed`)."""
    ONE = {0: ["a.cu"]}
    SHOWN = {0: {"a.cu": {50}}}

    def test_resolved_is_accepted_for_a_skipped_recheck_only_file(self):
        out = run_recheck_only(resolved_report("i1"), issue("a.cu", 50, id="i1"), self.ONE,
                               self.SHOWN, skipped=["a.cu"], recheck_only={"a.cu"})
        self.assertEqual(status(out, "i1"), "resolved")
        self.assertEqual((out["complete"], out["event"]), (True, "APPROVE"))

    def test_resolved_is_accepted_for_a_truncated_recheck_only_file(self):
        out = run_recheck_only(resolved_report("i1"), issue("a.cu", 50, id="i1"), self.ONE,
                               self.SHOWN, truncated=["a.cu"], recheck_only={"a.cu"})
        self.assertEqual(status(out, "i1"), "resolved")
        self.assertEqual((out["complete"], out["event"]), (True, "APPROVE"))

    def test_the_shown_rule_still_applies(self):
        out = run_recheck_only(resolved_report("i1"), issue("a.cu", 50, id="i1"), self.ONE,
                               {0: {"a.cu": {49}}}, skipped=["a.cu"], recheck_only={"a.cu"})
        self.assertEqual(status(out, "i1"), "open")

    def test_every_chunk_holding_the_file_must_be_ok(self):
        results = [res(0, [], prev=[("i1", "resolved")]), res(1, [], status="partial")]
        out = run_recheck_only(results, issue("a.cu", 50, id="i1"), {0: ["a.cu"], 1: ["a.cu"]},
                               self.SHOWN, skipped=["a.cu"], recheck_only={"a.cu"})
        self.assertEqual(status(out, "i1"), "open")
        self.assertFalse(out["complete"])

    def test_another_skipped_file_keeps_the_run_incomplete(self):
        out = run_recheck_only(resolved_report("i1"), issue("a.cu", 50, id="i1"), self.ONE,
                               self.SHOWN, skipped=["a.cu", "b.cc"], recheck_only={"a.cu"})
        self.assertEqual(status(out, "i1"), "resolved")
        self.assertEqual((out["complete"], out["event"]), (False, "COMMENT"))

    def test_a_skipped_recheck_only_file_with_an_open_issue_approves_without_a_gap(self):
        """The open issue is not blocking: APPROVE, and the head names no skipped file."""
        prev = issue("a.cu", 50, id="i1", blocking=False, pre="ask", located=True)
        for kw in ({"skipped": ["a.cu"]}, {"truncated": ["a.cu"]}):
            out = run_recheck_only([res(0, [], prev=[("i1", "open")])], prev, self.ONE,
                                   self.SHOWN, recheck_only={"a.cu"}, **kw)
            self.assertEqual((status(out, "i1"), out["complete"], out["event"], out["gaps"]),
                             ("open", True, "APPROVE", []), kw)
            body = render_body(out, [], META, {"issues": []})
            self.assertIn("**판정 근거:** 차단 사유 없음", body)
            self.assertNotIn("생략된 파일 있음", body)
            self.assertNotIn("잘린 변경 있음", body)
        # left out: the skipped file counts again, and the reason says so
        out = run_recheck_only([res(0, [], prev=[("i1", "open")])], prev, self.ONE,
                               self.SHOWN, skipped=["a.cu"], recheck_only={"a.cu"},
                               left_out=["i2"])
        self.assertEqual((out["event"], out["gaps"]), ("COMMENT", ["a.cu"]))
        self.assertIn("생략된 파일 있음 (조각 수 한도)", render_body(out, [], META, {"issues": []}))

    def test_a_left_out_recheck_keeps_the_run_incomplete(self):
        out = run_recheck_only(resolved_report("i1"), issue("a.cu", 50, id="i1"), self.ONE,
                               self.SHOWN, skipped=["a.cu"], recheck_only={"a.cu"},
                               left_out=["i2"])
        self.assertEqual(status(out, "i1"), "resolved")
        self.assertEqual((out["complete"], out["event"]), (False, "COMMENT"))
