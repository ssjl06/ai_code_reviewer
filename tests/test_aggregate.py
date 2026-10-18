import unittest

from review.aggregate import finding_id, merge, normalize_path, split_inline
from review.config import load_config

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


def run(results, prev=(), changed=None, delta=None, no_targets=False):
    return merge(results, CFG, list(prev), changed, delta, no_targets)


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
        r = run([res(0, [F(line=10), F(line=50)])], changed={"a.cu": {50}}, delta={"a.cu"})
        self.assertEqual([f["line"] for f in r["findings"]], [50])
        self.assertEqual(r["dropped"], 1)

    def test_previous_open_blocks(self):
        r = run([res(0, [], prev=[("f1", "open")])], [P(blocking=True)], {}, {"a.cu"})
        self.assertEqual(r["event"], "REQUEST_CHANGES")

    def test_previous_untouched_file_stays_open(self):
        r = run([res(0, [])], [P(file="b.cu", blocking=True)], {}, {"a.cu"})
        self.assertEqual(r["previous"][0]["status"], "open")
        self.assertEqual(r["event"], "REQUEST_CHANGES")

    def test_previous_unreported_touched_is_unclear_not_blocking(self):
        r = run([res(0, [])], [P(blocking=True)], {}, {"a.cu"})
        self.assertEqual(r["previous"][0]["status"], "unclear")
        self.assertEqual(r["event"], "APPROVE")

    def test_previous_resolved_approves(self):
        r = run([res(0, [], prev=[("f1", "resolved")])], [P(blocking=True)], {}, {"a.cu"})
        self.assertEqual((r["previous"][0]["status"], r["event"]), ("resolved", "APPROVE"))

    def test_previous_worst_status_wins(self):
        r = run([res(0, [], prev=[("f1", "resolved")]), res(1, [], prev=[("f1", "open")])],
                [P(blocking=True)], {}, {"a.cu"})
        self.assertEqual(r["previous"][0]["status"], "open")

    def test_previous_rereported_as_new_removed(self):
        f = F(line=10, title="t")
        pid = finding_id({"file": "a.cu", "line": 10, "title": "t"})
        r = run([res(0, [f])], [P(id=pid)], {"a.cu": {10}}, {"a.cu"})
        self.assertEqual(r["findings"], [])

    def test_dedupe_keeps_stronger(self):
        r = run([res(0, [F(line=10, sev="minor", conf=90)]),
                 res(1, [F(line=11, sev="major", conf=85)])])
        self.assertEqual([(f["line"], f["severity"]) for f in r["findings"]], [(11, "major")])

    def test_dedupe_tie_keeps_earlier_and_other_category_kept(self):
        r = run([res(0, [F(line=10), F(line=12, title="x"), F(line=11, cat="style")])])
        self.assertEqual(sorted(f["line"] for f in r["findings"]), [10, 11])

    def test_normalize_paths_and_lines(self):
        r = run([res(0, [F(file="./src/a.cu", line=0), F(file="b/src/a.cu", line=12)])])
        self.assertEqual({f["file"] for f in r["findings"]}, {"src/a.cu"})
        inline, body = split_inline(r["findings"], {"src/a.cu": {12}})
        self.assertEqual(([f["line"] for f in inline], [f["line"] for f in body]), ([12], [0]))
        self.assertEqual(normalize_path("a/a/x"), "a/x")

    def test_finding_id_stable(self):
        f = {"file": "a", "line": 1, "title": "t"}
        self.assertEqual(finding_id(f), finding_id(dict(f)))
        self.assertEqual(len(finding_id(f)), 8)

    def test_sorted_and_summaries(self):
        r = run([res(1, [F(sev="minor", conf=90, line=1)], summary="two"),
                 res(0, [F(sev="critical", conf=60, line=5)], summary="one", reason="turns")])
        self.assertEqual([f["severity"] for f in r["findings"]], ["critical", "minor"])
        self.assertEqual(r["summaries"], ["[c1] one (미완료: turns)", "[c2] two"])

    def test_previous_note_carried(self):
        results = [res(0, [])]
        results[0]["output"]["previous_findings"] = [{"id": "f1", "status": "open", "note": "n"}]
        r = run(results, [P()], {}, {"a.cu"})
        self.assertEqual(r["previous"][0]["note"], "n")


if __name__ == "__main__":
    unittest.main()
