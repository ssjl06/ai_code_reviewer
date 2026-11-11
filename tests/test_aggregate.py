import unittest

from review.aggregate import finding_id, merge, normalize_path, split_inline
from review.config import load_config
from review.render import render_body

CFG = load_config()


def F(file="a.cu", line=10, sev="major", conf=90, cat="bug", title="t"):
    return {"file": file, "line": line, "severity": sev, "confidence": conf,
            "category": cat, "title": title, "detail": "d"}


def P(id="f1", file="a.cu", line=10, blocking=False, sev="major"):
    return {"id": id, "file": file, "line": line, "title": "t",
            "severity": sev, "blocking": blocking}


def res(i, findings, status="ok", prev=(), summary="s", reason=None):
    out = None
    if status != "failed":
        out = {"summary": summary, "findings": findings,
               "previous_findings": [{"id": a, "status": b} for a, b in prev],
               "incomplete_reason": reason}
    return {"index": i, "status": status, "output": out, "stats": {},
            "session_id": None, "error": None}


def run(results, prev=(), changed=None, no_targets=False, skipped=None):
    return merge(results, CFG, list(prev), changed, no_targets, skipped=skipped)


class AggregateTest(unittest.TestCase):
    def test_blocking_boundary_79_80(self):
        r = run([res(0, [F(sev="major", conf=79)])])
        self.assertEqual(r["event"], "APPROVE")
        self.assertTrue(r["findings"][0]["needs_check"])
        r = run([res(0, [F(sev="major", conf=80)])])
        self.assertEqual(r["event"], "REQUEST_CHANGES")
        self.assertTrue(r["findings"][0]["blocking"])

    def test_minor_high_confidence_not_blocking(self):
        self.assertEqual(run([res(0, [F(sev="minor", conf=99)])])["event"], "APPROVE")

    def test_low_confidence_dropped(self):
        r = run([res(0, [F(conf=49)])])
        self.assertEqual((r["findings"], r["dropped"]), ([], 1))

    def test_incomplete_blocks_approve(self):
        r = run([res(0, []), res(1, [], status="incomplete")])
        self.assertEqual((r["event"], r["incomplete"]), ("COMMENT", [1]))

    def test_blocking_wins_over_incomplete(self):
        r = run([res(0, [F(sev="critical", conf=95)]), res(1, [], status="incomplete")])
        self.assertEqual((r["event"], r["incomplete"]), ("REQUEST_CHANGES", [1]))

    def test_all_failed(self):
        r = run([res(0, [], status="failed")])
        self.assertEqual((r["event"], r["failed_all"]), ("COMMENT", True))

    def test_no_chunks_is_comment(self):
        r = run([], no_targets=True)
        self.assertEqual((r["event"], r["no_targets"]), ("COMMENT", True))
        self.assertFalse(r["failed_all"])

    def test_incremental_filter_drops_unchanged_lines(self):
        r = run([res(0, [F(line=10), F(line=50)])], changed={"a.cu": {50}})
        self.assertEqual([f["line"] for f in r["findings"]], [50])
        self.assertEqual(r["dropped"], 1)

    def test_previous_open_blocks(self):
        r = run([res(0, [], prev=[("f1", "open")])], [P(blocking=True)], {})
        self.assertEqual(r["event"], "REQUEST_CHANGES")

    def test_previous_untouched_file_stays_open(self):
        r = run([res(0, [])], [P(file="b.cu", blocking=True)], {})
        self.assertEqual(r["previous"][0]["status"], "open")
        self.assertEqual(r["event"], "REQUEST_CHANGES")

    def test_model_note_does_not_make_the_review_incomplete(self):
        r = run([res(0, [F(sev="minor")], reason="x")])
        self.assertEqual((len(r["findings"]), r["event"], r["complete"], r["incomplete"]),
                         (1, "APPROVE", True, []))

    def test_partial_keeps_findings_but_blocks_approve(self):
        r = run([res(0, [F(sev="minor")], status="partial", reason="x")])
        self.assertEqual((len(r["findings"]), r["event"], r["complete"], r["incomplete"]),
                         (1, "COMMENT", False, [0]))
        self.assertEqual(r["summaries"], ["[c1] s (참고: x)"])

    def test_partial_blocking_finding_requests_changes(self):
        r = run([res(0, [F()], status="partial", reason="x")])
        self.assertEqual(r["event"], "REQUEST_CHANGES")

    def test_incomplete_chunk_output_is_not_used(self):
        r = run([res(0, [F()], status="incomplete")])
        self.assertEqual((r["findings"], r["event"], r["incomplete"]), ([], "COMMENT", [0]))

    def test_skipped_files_block_approve(self):
        r = run([res(0, [])], skipped=["big.cu"])
        self.assertEqual((r["event"], r["skipped"], r["complete"]), ("COMMENT", ["big.cu"], False))

    def test_cut_files_never_approve_and_keep_the_run_complete(self):
        r = merge([res(0, [])], CFG, [], None, False, cut=["b.js", "a.js", "b.js"])
        self.assertEqual((r["event"], r["cut"], r["complete"]), ("COMMENT", ["a.js", "b.js"], True))
        self.assertEqual(run([res(0, [])])["cut"], [])

    def test_cut_files_do_not_soften_a_block(self):
        r = merge([res(0, [F()])], CFG, [], None, False, cut=["a.js"])
        self.assertEqual(r["event"], "REQUEST_CHANGES")

    def test_unclear_nonblocking_previous_still_approves(self):
        r = run([res(0, [])], [dict(P(blocking=False), pre="ask", located=False)], {})
        self.assertEqual((r["previous"][0]["status"], r["event"]), ("unclear", "APPROVE"))

    def test_approvable(self):
        r = run([res(0, [F()])])
        self.assertEqual((r["event"], r["approvable"]), ("REQUEST_CHANGES", True))
        self.assertTrue(run([res(0, [])])["approvable"])
        self.assertFalse(run([], no_targets=True)["approvable"])
        self.assertFalse(run([res(0, [], status="incomplete")])["approvable"])
        self.assertFalse(run([res(0, [])], skipped=["big.cu"])["approvable"])
        self.assertFalse(merge([res(0, [])], CFG, [], None, False, cut=["a.js"])["approvable"])

    def test_unclear_ids(self):
        r = run([res(0, [])], [dict(P(blocking=True), pre="ask", located=False),
                               dict(P("f2", blocking=False), pre="ask", located=False)], {})
        self.assertEqual((r["unclear"], r["event"]), (["f1"], "COMMENT"))
        self.assertEqual(run([res(0, [])])["unclear"], [])

    def test_complete_when_all_ok(self):
        self.assertTrue(run([res(0, [])])["complete"])
    def test_previous_resolved_approves(self):
        r = run([res(0, [], prev=[("f1", "resolved")])], [P(blocking=True)], {})
        self.assertEqual((r["previous"][0]["status"], r["event"]), ("resolved", "APPROVE"))

    def test_previous_worst_status_wins(self):
        r = run([res(0, [], prev=[("f1", "resolved")]), res(1, [], prev=[("f1", "open")])],
                [P(blocking=True)], {})
        self.assertEqual(r["previous"][0]["status"], "open")

    def test_previous_rereported_as_new_removed(self):
        f = F(line=10, title="t", sev="minor")
        pid = finding_id({"file": "a.cu", "line": 10, "title": "t"})
        r = run([res(0, [f])], [P(id=pid, sev="minor")], {"a.cu": {10}})
        self.assertEqual(r["findings"], [])
        # an exact blocking re-report of the open issue is a link to it
        r = run([res(0, [F(line=10, title="t")])], [P(id=pid)], {"a.cu": {10}})
        self.assertEqual((r["findings"], [x["id"] for x in r["links"]]), ([], [pid]))
        # reworded, without `same_as`: new (rev 9), under another id
        r = run([res(0, [F(line=10, title="u")])], [P(id=pid)], {"a.cu": {10}})
        self.assertEqual([(x["near_issue"], x["id"] != pid) for x in r["findings"]],
                         [(pid, True)])

    def test_dedupe_keeps_stronger(self):
        r = run([res(0, [F(line=10, sev="minor", conf=90)]),
                 res(1, [F(line=11, sev="major", conf=85)])])
        self.assertEqual([(f["line"], f["severity"]) for f in r["findings"]], [(11, "major")])

    def test_dedupe_tie_keeps_earlier_and_other_category_kept(self):
        r = run([res(0, [F(line=10, sev="minor"), F(line=12, title="x", sev="minor"),
                         F(line=11, cat="style", sev="minor")])])
        self.assertEqual(sorted(f["line"] for f in r["findings"]), [10, 11])

    def test_dedupe_folds_a_reworded_neighbor(self):
        r = run([res(0, [F(line=10, title="a", sev="minor")]),
                 res(1, [F(line=11, title="b", sev="minor")])])
        self.assertEqual([(f["line"], f["title"]) for f in r["findings"]], [(10, "a")])
        self.assertEqual([(f["line"], f["dup_of"]) for f in r["folded"]], [(11, "이번 실행 a.cu:10")])
        self.assertEqual(r["overlap"], [])

    def test_an_overlap_copy_is_not_folded(self):
        r = run([res(0, [F(line=10, title="a")]), res(1, [F(line=10, title="a")])])
        self.assertEqual([f["line"] for f in r["findings"]], [10])
        self.assertEqual(r["folded"], [])
        self.assertEqual([f["line"] for f in r["overlap"]], [10])

    def test_two_distinct_folded_findings_are_both_listed(self):
        """One folded by `classify` (B next to the open issue), one by `_dedupe` (C next to B)."""
        r = run([res(0, [F(line=52, title="B", sev="minor"),
                         F(line=53, title="C", sev="minor")])],
                [P(id="A", line=50, sev="minor")], {"a.cu": {52, 53}})
        self.assertEqual(r["findings"], [])
        self.assertEqual(sorted((f["title"], f["dup_of"]) for f in r["folded"]),
                         [("B", "A"), ("C", "이번 실행 a.cu:52")])

    def test_a_fold_into_an_issue_resolved_in_the_same_run_is_a_new_finding(self):
        prev = dict(P(id="A", line=50), pre="ask", located=True, lo=50, hi=50)
        r = merge([res(0, [F(line=52, title="B")], prev=[("A", "resolved")])], CFG, [prev],
                  {"a.cu": {52}}, False, chunk_files={0: ["a.cu"]})
        self.assertEqual(r["previous"][0]["status"], "resolved")
        self.assertEqual(([(f["line"], f["near_issue"]) for f in r["findings"]], r["folded"],
                          r["duplicates"]), ([(52, "A")], [], []))
        body = render_body(dict(r, previous=[]), [], {"model": "m", "chunks": 1, "duration_s": 1,
                                                       "range": "r", "reviewed": [], "skipped": []},
                           {"issues": []})
        self.assertNotIn("→ 이슈 A", body)

    def test_dedupe_removes_a_blocking_finding_only_as_a_copy(self):
        a = dict(F(line=10, title="a", conf=90), same_as="X")
        r = run([res(0, [a]), res(1, [dict(a, confidence=95)])])
        self.assertEqual([(f["line"], f["confidence"]) for f in r["findings"]], [(10, 95)])
        self.assertEqual(([f["confidence"] for f in r["overlap"]], r["folded"]), ([90], []))
        r = run([res(0, [a]), res(1, [dict(a, same_as="Y")])])
        self.assertEqual(([f.get("same_as") for f in r["findings"]], r["overlap"], r["folded"]),
                         (["X", "Y"], [], []))

    def test_dedupe_never_folds_a_blocking_neighbor(self):
        r = run([res(0, [F(line=10, title="a")]), res(1, [F(line=11, title="b")])])
        self.assertEqual(sorted(f["line"] for f in r["findings"]), [10, 11])
        self.assertEqual((r["folded"], r["overlap"]), ([], []))

    def test_a_non_blocking_finding_is_never_kept_over_a_blocking_one(self):
        r = run([res(0, [F(line=50, sev="critical", conf=70, title="weak")]),
                 res(1, [F(line=51, sev="major", conf=95, title="strong")])])
        self.assertEqual([(f["line"], f["blocking"]) for f in r["findings"]], [(51, True)])
        self.assertEqual([(f["line"], f["dup_of"]) for f in r["folded"]],
                         [(50, "이번 실행 a.cu:51")])
        self.assertEqual(r["event"], "REQUEST_CHANGES")

    def test_normalize_paths_and_lines(self):
        r = run([res(0, [F(file="./src/a.cu", line=0), F(file="b/src/a.cu", line=12)])])
        self.assertEqual({f["file"] for f in r["findings"]}, {"src/a.cu"})
        inline, body = split_inline(r["findings"], {"src/a.cu": {12}})
        self.assertEqual(([f["line"] for f in inline], [f["line"] for f in body]), ([12], [0]))
        self.assertEqual(normalize_path("a/a/x"), "a/x")

    def test_literal_a_b_dirs_kept_when_known(self):
        r = merge([res(0, [F(file="a/module.py", line=3, sev="critical")])], CFG, [],
                  {"a/module.py": {3}}, False)
        self.assertEqual(r["event"], "REQUEST_CHANGES")
        self.assertEqual(r["findings"][0]["file"], "a/module.py")
        r = merge([res(0, [F(file="b/src/x.py", line=3, sev="critical")])], CFG, [],
                  {"src/x.py": {3}}, False)
        self.assertEqual([f["file"] for f in r["findings"]], ["src/x.py"])
        r = merge([res(0, [F(file="./a/m.py", line=3, sev="critical")])], CFG, [], None, False,
                  chunk_files={0: ["a/m.py"]})
        self.assertEqual([f["file"] for f in r["findings"]], ["a/m.py"])
        self.assertEqual(normalize_path("b/a/m.py", {"a/m.py"}), "a/m.py")

    def test_prefix_resolved_by_reporting_chunk(self):
        changed = {"src/x.py": {10}, "b/src/x.py": {20}}
        files = {0: ["src/x.py"], 1: ["b/src/x.py"]}
        r = merge([res(0, [F(file="b/src/x.py", line=10, sev="critical")]), res(1, [])], CFG,
                  [], changed, False, chunk_files=files)
        self.assertEqual(r["event"], "REQUEST_CHANGES")
        self.assertEqual([f["file"] for f in r["findings"]], ["src/x.py"])
        r = merge([res(0, []), res(1, [F(file="b/src/x.py", line=20, sev="critical")])], CFG,
                  [], changed, False, chunk_files=files)
        self.assertEqual([f["file"] for f in r["findings"]], ["b/src/x.py"])

    def test_prefix_ambiguous_in_one_chunk_resolved_by_line(self):
        changed = {"src/x.py": {10}, "b/src/x.py": {20}}
        files = {0: ["src/x.py", "b/src/x.py"]}
        r = merge([res(0, [F(file="b/src/x.py", line=10, sev="critical")])], CFG, [], changed,
                  False, chunk_files=files)
        self.assertEqual(r["event"], "REQUEST_CHANGES")
        self.assertEqual([(f["file"], f["line"]) for f in r["findings"]], [("src/x.py", 10)])
        r = merge([res(0, [F(file="b/src/x.py", line=20, sev="critical")])], CFG, [], changed,
                  False, chunk_files=files)
        self.assertEqual([(f["file"], f["line"]) for f in r["findings"]], [("b/src/x.py", 20)])

    def test_finding_id_stable(self):
        f = {"file": "a", "line": 1, "title": "t"}
        self.assertEqual(finding_id(f), finding_id(dict(f)))
        self.assertEqual(len(finding_id(f)), 8)

    def test_sorted_and_summaries(self):
        r = run([res(1, [F(sev="minor", conf=90, line=1)], summary="two"),
                 res(0, [F(sev="critical", conf=60, line=5)], summary="one", reason="turns")])
        self.assertEqual([f["severity"] for f in r["findings"]], ["critical", "minor"])
        self.assertEqual(r["summaries"], ["[c1] one (참고: turns)", "[c2] two"])

    def test_model_note_reaches_pr_body(self):
        r = run([res(0, [], summary="one", reason="호출부를 확인하지 못했다")])
        meta = {"model": "m", "chunks": 1, "duration_s": 1, "range": "a..b", "mode": "full",
                "reviewed": ["a.cu"], "skipped": []}
        body = render_body(r, [], meta, {"v": 2})
        self.assertIn("[c1] one (참고: 호출부를 확인하지 못했다)", body)
        self.assertNotIn("미완료", body)

    def test_previous_note_carried(self):
        results = [res(0, [])]
        results[0]["output"]["previous_findings"] = [{"id": "f1", "status": "open", "note": "n"}]
        r = run(results, [P()], {})
        self.assertEqual(r["previous"][0]["note"], "n")

    def test_errors_listed_for_non_ok(self):
        bad = res(1, [], status="failed")
        bad["error"] = "E" * 300
        slow = dict(res(2, [], status="incomplete"), error="timeout")
        r = run([res(0, []), bad, slow])
        self.assertEqual(r["errors"], [[1, "E" * 200], [2, "timeout"]])
        self.assertEqual(run([res(0, [])])["errors"], [])


def Q(id, line, sev, blocking, status="open", **kw):
    """A full ledger issue in a.cu."""
    return dict({"id": id, "file": "a.cu", "line": line, "lo": line, "hi": line,
                 "title": "t-" + id, "severity": sev, "blocking": blocking, "status": status,
                 "orphan": False, "anchor": "", "excerpt": ""}, **kw)


def run1(findings, previous=(), closed=(), prev=(), lines=None):
    """One ok chunk holding a.cu; `lines` the changed lines (default: the findings' lines)."""
    lines = {f["line"] for f in findings} if lines is None else lines
    return merge([res(0, findings, prev=prev)], CFG, list(previous), {"a.cu": set(lines)},
                 False, chunk_files={0: ["a.cu"]}, closed=list(closed))


class SameAsReachTest(unittest.TestCase):
    """Review C1/I2: a `same_as` merges only into an issue within reach of the finding."""

    def test_an_orphan_is_relinked_past_a_far_more_severe_member(self):
        t = Q("T", 10, "major", True, orphan=True, pre="orphan", located=True)
        x = Q("X", 300, "critical", True, via="T", pre="ask", located=True)
        r = run1([dict(F(line=105, title="d"), same_as="T")], [t, x], prev=[("X", "resolved")])
        self.assertEqual(r["event"], "REQUEST_CHANGES")
        self.assertEqual([(p["id"], p["status"], p["line"]) for p in r["previous"]],
                         [("T", "open", 105), ("X", "resolved", 300)])

    def test_a_far_closed_member_does_not_swallow_a_more_severe_finding(self):
        c = Q("C", 100, "minor", False, status="resolved")
        x = Q("X", 300, "critical", False, status="resolved", via="C")
        r = run1([dict(F(line=105, title="d"), same_as="C")], closed=[c, x])
        self.assertEqual((r["event"], [(f["line"], f["via"]) for f in r["findings"]]),
                         ("REQUEST_CHANGES", [(105, "C")]))


class PositionNeverLowersBlockingTest(unittest.TestCase):
    """Rev 9: only a `same_as` merges, swallows or un-blocks a blocking finding."""

    def test_codex1_a_distinct_blocking_finding_near_an_open_issue_is_its_own_issue(self):
        a = Q("A", 50, "major", True, pre="open", located=True)
        r = run1([F(line=52, sev="major", conf=95, title="B")], [a])
        self.assertEqual((r["event"], r["folded"], r["duplicates"]), ("REQUEST_CHANGES", [], []))
        f, = r["findings"]
        self.assertEqual((f["line"], f["blocking"], f["near_issue"], "folded" in f),
                         (52, True, "A", False))
        # next run: A is reported resolved and B is not reported (its line did not change)
        b = dict(Q(f["id"], 52, "major", True), pre="ask", located=True)
        r2 = run1([], [dict(a, pre="ask"), b], prev=[("A", "resolved")], lines=set())
        self.assertEqual([(p["id"], p["status"]) for p in r2["previous"]],
                         [("A", "resolved"), (f["id"], "open")])
        self.assertEqual(r2["event"], "REQUEST_CHANGES")

    def test_a_fold_into_an_issue_resolved_in_this_run_is_now_a_new_finding(self):
        a = Q("A", 50, "critical", True, pre="ask", located=True)
        r = run1([F(line=52, sev="critical", conf=95, title="other defect")], [a],
                 prev=[("A", "resolved")])
        self.assertEqual((r["event"], r["folded"], r["duplicates"]), ("REQUEST_CHANGES", [], []))
        f, = r["findings"]
        self.assertEqual((f["line"], f["blocking"], f["near_issue"], "promoted" in f),
                         (52, True, "A", False))
        self.assertEqual(r["previous"][0]["status"], "resolved")

    def test_codex2_same_as_drops_one_and_position_does_not_drop_the_other(self):
        c = Q("C", 50, "critical", True, status="resolved")
        a = dict(F(line=60, sev="critical", conf=99, title="A"), same_as="C")
        b = F(line=62, sev="critical", conf=95, title="B")
        r = run1([a, b], closed=[c])
        self.assertEqual([d["dup_of"] for d in r["duplicates"]], ["C"])
        self.assertEqual([(f["line"], f["blocking"]) for f in r["findings"]], [(62, True)])
        self.assertEqual(r["event"], "REQUEST_CHANGES")

    def test_codex4_a_blocking_finding_near_a_resolved_critical_blocks(self):
        c = Q("C", 100, "critical", True, status="resolved")
        r = run1([F(line=102, sev="major", conf=95)], closed=[c])
        f, = r["findings"]
        self.assertEqual((f["blocking"], f["needs_check"], f["near_closed"], r["event"]),
                         (True, False, "C", "REQUEST_CHANGES"))

    def test_a_blocking_reraise_next_to_a_waived_issue_blocks(self):
        """The accepted cost: without `same_as` a blocking re-raise is new and blocks."""
        w = Q("W", 50, "critical", True, status="waived")
        m = Q("M", 53, "minor", False, pre="open", located=True)
        r = run1([F(line=51, sev="critical", conf=95, title="again")], [m], closed=[w])
        self.assertEqual(([(f["line"], f["near_closed"]) for f in r["findings"]], r["event"]),
                         ([(51, "W")], "REQUEST_CHANGES"))
        r = run1([dict(F(line=51, sev="critical", conf=95, title="again"), same_as="W")], [m],
                 closed=[w])
        self.assertEqual((r["findings"], r["event"]), ([], "APPROVE"))

    def test_a_non_blocking_finding_near_an_open_issue_is_still_folded(self):
        a = Q("A", 50, "minor", False, pre="open", located=True)
        r = run1([F(line=52, sev="minor", conf=95, title="reworded")], [a])
        self.assertEqual((r["event"], r["findings"]), ("APPROVE", []))
        self.assertEqual([(f["line"], f["dup_of"], f["folded"]) for f in r["folded"]],
                         [(52, "A", True)])
        body = render_body(r, [], {"model": "m", "chunks": 1, "duration_s": 1, "range": "r",
                                   "reviewed": [], "skipped": []}, {"issues": []})
        self.assertIn("### 기존 이슈에 합쳐진 지적 (1건)", body)

    def test_a_non_blocking_finding_near_only_a_closed_issue_is_new_with_a_note(self):
        c = Q("C", 50, "major", True, status="resolved")
        r = run1([F(line=52, sev="major", conf=70, title="weak")], closed=[c])
        f, = r["findings"]
        self.assertEqual((f["near_closed"], f["blocking"], f["needs_check"], r["event"]),
                         ("C", False, True, "APPROVE"))


class LinkBeatsResolvedTest(unittest.TestCase):
    """Codex #3: a valid `same_as` link is open evidence and wins over a resolved report."""

    def two_chunks(self, x_blocking, sev="major", conf=95):
        c = Q("C", 100, "minor", False, status="resolved")
        x = Q("X", 110, "critical", x_blocking, via="C", pre="ask", located=True)
        f = dict(F(line=115, sev=sev, conf=conf, title="again"), same_as="C")
        return merge([res(0, [], prev=[("X", "resolved")]), res(1, [f])], CFG, [x],
                     {"a.cu": {115}}, False, chunk_files={0: ["a.cu"], 1: ["a.cu"]},
                     closed=[c])

    def test_a_link_reopens_an_issue_reported_resolved_in_the_same_run(self):
        r = self.two_chunks(True)
        self.assertEqual([x["id"] for x in r["links"]], ["X"])
        p, = r["previous"]
        self.assertEqual((p["status"], p["blocking"], p.get("reopened")), ("open", True, True))
        self.assertTrue(r["links"][0]["reopened"])
        self.assertEqual(r["event"], "REQUEST_CHANGES")

    def test_a_blocking_link_makes_its_issue_blocking(self):
        r = self.two_chunks(False)
        p, = r["previous"]
        self.assertEqual((p["status"], p["blocking"], r["event"]),
                         ("open", True, "REQUEST_CHANGES"))
        # a non-blocking link reopens but does not make the issue blocking
        r = self.two_chunks(False, conf=70)
        p, = r["previous"]
        self.assertEqual((p["status"], p["blocking"], r["event"]), ("open", False, "APPROVE"))

    def test_a_file_that_left_the_pr_stays_resolved(self):
        x = Q("X", 110, "critical", True, pre="resolved", located=True)
        f = dict(F(line=112, sev="major", conf=95), same_as="X")
        r = merge([res(0, [f])], CFG, [x], {"a.cu": {112}}, False, chunk_files={0: ["a.cu"]})
        self.assertEqual(([x["id"] for x in r["links"]], r["previous"][0]["status"]),
                         (["X"], "resolved"))
        self.assertNotIn("reopened", r["previous"][0])


class ExactReReportMergeTest(unittest.TestCase):
    def test_an_exact_re_report_of_an_open_blocking_issue_converges(self):
        a = Q("A", 50, "major", True, pre="ask", located=True)
        r = run1([dict(F(line=50, conf=95), title="t-A")], [a])
        self.assertEqual((r["findings"], [x["id"] for x in r["links"]], r["event"]),
                         ([], ["A"], "REQUEST_CHANGES"))
        # it overrides a same-run resolved report, like a same_as link
        r = run1([dict(F(line=50, conf=95), title="t-A")], [a], prev=[("A", "resolved")])
        self.assertEqual((r["previous"][0]["status"], r["event"]), ("open", "REQUEST_CHANGES"))

    def test_other_title_or_more_severe_or_closed_is_new(self):
        a = Q("A", 50, "major", True, pre="ask", located=True)
        for f in (dict(F(line=50, conf=95), title="other"),
                  dict(F(line=50, sev="critical", conf=95), title="t-A")):
            r = run1([f], [a])
            self.assertEqual(([x["blocking"] for x in r["findings"]], r["links"]), ([True], []))
        c = Q("C", 50, "major", True, status="resolved")
        r = run1([dict(F(line=50, conf=95), title="t-C")], closed=[c])
        self.assertEqual(([x["near_closed"] for x in r["findings"]], r["links"], r["event"]),
                         (["C"], [], "REQUEST_CHANGES"))


class DedupeKeepsEvidenceTest(unittest.TestCase):
    """Review M1: a link from a finding `_dedupe` removed still counts as open evidence."""

    def x(self, **kw):
        return Q("X", 100, "major", True, pre="ask", located=True, **kw)

    def test_a_removed_same_as_finding_still_reopens_its_issue(self):
        n = dict(F(line=102, sev="minor", conf=60, title="N"), same_as="X")
        m = F(line=101, sev="minor", conf=90, title="M")
        r = run1([n, m], [self.x()], prev=[("X", "resolved")])
        self.assertEqual([f["title"] for f in r["folded"] if f["line"] == 102], ["N"])
        self.assertEqual(([x["id"] for x in r["links"]], r["previous"][0]["status"], r["event"]),
                         (["X"], "open", "REQUEST_CHANGES"))

    def test_a_same_as_finding_folded_into_a_blocking_one_keeps_its_issue_open(self):
        n = dict(F(line=102, sev="minor", conf=60, title="N"), same_as="X")
        b = F(line=103, sev="major", conf=95, title="B")
        r = run1([n, b], [self.x()], prev=[("X", "resolved")])
        self.assertEqual([(f["title"], f["blocking"]) for f in r["findings"]], [("B", True)])
        self.assertEqual(r["previous"][0]["status"], "open")

    def test_a_removed_exact_re_report_still_reopens_its_issue(self):
        n = F(line=100, sev="minor", conf=60, title="t-X")
        m = F(line=101, sev="minor", conf=90, title="M")
        r = run1([n, m], [self.x()], prev=[("X", "resolved")])
        self.assertEqual((r["previous"][0]["status"], r["event"]), ("open", "REQUEST_CHANGES"))

    def test_an_ignored_same_as_is_open_evidence_without_blocking(self):
        x = Q("X", 100, "minor", False, pre="ask", located=True)
        f = dict(F(line=105, sev="critical", conf=95, title="C"), same_as="X")
        r = run1([f], [x], prev=[("X", "resolved")])
        self.assertEqual([(g["same_as_ignored"], g["via"]) for g in r["findings"]], [("X", "X")])
        p, = r["previous"]
        self.assertEqual((p["status"], p["blocking"], p.get("reopened")), ("open", False, True))
        self.assertEqual(r["links"], [])
        self.assertEqual([(e["id"], e.get("reopened")) for e in r["evidence"]], [("X", True)])


if __name__ == "__main__":
    unittest.main()
