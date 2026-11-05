import unittest

from review.aggregate import merge
from tests.test_aggregate import CFG, F, P, res


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

    def test_more_blocking_rereport_is_merged_not_kept(self):
        r = run([res(0, [F(title="other", sev="critical", conf=95)])], [self.prev(False)],
                anchor=lambda p, l: "aaaa0000")
        self.assertEqual(r["findings"], [])
        self.assertEqual(len(r["duplicates"]), 1)
        self.assertEqual(r["duplicates"][0]["dup_of"], "p1")
        self.assertFalse(r["previous"][0]["blocking"])  # the issue keeps its severity
        self.assertEqual(r["event"], "APPROVE")

    def test_not_more_blocking_rereport_dropped(self):
        for prev_blocking, sev in ((True, "critical"), (False, "minor")):
            r = run([res(0, [F(title="other", sev=sev, conf=95)])], [self.prev(prev_blocking)],
                    anchor=lambda p, l: "aaaa0000")
            self.assertEqual(r["findings"], [])

    def test_new_ids_never_collide_with_known_ids(self):
        from review.ledger import finding_id
        taken = finding_id(F(line=10, title="t"))
        closed = [dict(P(id=taken, line=300), status="resolved")]
        r = run([res(0, [F(line=10)])], [], closed=closed)
        self.assertEqual(len(r["findings"]), 1)
        self.assertNotEqual(r["findings"][0]["id"], taken)

    def test_closed_issue_swallows_a_critical_reraise(self):
        closed = [dict(P(id="c1"), status="resolved")]
        r = run([res(0, [F(sev="critical", conf=95)])], [], closed=closed)
        self.assertEqual((r["findings"], len(r["duplicates"]), r["event"]), ([], 1, "APPROVE"))

    def test_truncated_makes_incomplete_comment(self):
        r = run([res(0, [])], truncated=["big.py"])
        self.assertEqual((r["event"], r["complete"], r["truncated"]), ("COMMENT", False, ["big.py"]))
        self.assertTrue(run([res(0, [])])["complete"])


if __name__ == "__main__":
    unittest.main()
