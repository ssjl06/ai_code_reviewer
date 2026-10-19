import unittest

from review.aggregate import finding_id, merge
from tests.test_aggregate import CFG, F, P as P0, res


def P(anchor="", pre=None, located=False, **kw):
    p = P0(**kw)
    p["anchor"] = anchor
    if pre is not None:
        p["pre"] = pre
        p["located"] = located
    return p


def run(results, prev=(), anchor=None):
    return merge(results, CFG, list(prev), None, None, False, anchor=anchor)


class AnchorMergeTest(unittest.TestCase):
    def test_id_survives_line_shift(self):
        fn = lambda path, line: "aaaa0000"
        a = run([res(0, [F(line=10)])], anchor=fn)["findings"][0]["id"]
        b = run([res(0, [F(line=30)])], anchor=fn)["findings"][0]["id"]
        self.assertEqual(a, b)

    def test_id_without_anchor_unchanged(self):
        self.assertEqual(finding_id({"file": "a", "line": 1, "title": "t"}),
                         finding_id({"file": "a", "line": 1, "title": "t", "anchor": ""}))

    def test_rereport_same_anchor_other_title_dropped(self):
        r = run([res(0, [F(title="other")])],
                [P(anchor="aaaa0000", pre="ask", located=True)],
                anchor=lambda p, l: "aaaa0000")
        self.assertEqual(r["findings"], [])

    def test_empty_anchor_does_not_drop(self):
        r = run([res(0, [F(title="other")])], [P(anchor="")], anchor=lambda p, l: "")
        self.assertEqual(len(r["findings"]), 1)

    def test_pre_resolved_without_report(self):
        r = run([], [P(blocking=True, pre="resolved")])
        self.assertEqual((r["previous"][0]["status"], r["event"]), ("resolved", "APPROVE"))

    def test_pre_open_without_report(self):
        r = run([res(0, [])], [P(blocking=True, pre="open")])
        self.assertEqual((r["previous"][0]["status"], r["event"]), ("open", "REQUEST_CHANGES"))

    def test_pre_ask_located_unreported_stays_open_blocking(self):
        r = run([res(0, [])], [P(blocking=True, pre="ask", located=True)])
        self.assertEqual((r["previous"][0]["status"], r["event"]), ("open", "REQUEST_CHANGES"))

    def test_pre_ask_not_located_is_unclear(self):
        r = run([res(0, [])], [P(blocking=True, pre="ask", located=False)])
        self.assertEqual((r["previous"][0]["status"], r["event"]), ("unclear", "COMMENT"))
        self.assertTrue(r["unclear_blocking"])

    def test_model_report_beats_pre(self):
        r = run([res(0, [], prev=[("f1", "resolved")])],
                [P(blocking=True, pre="ask", located=True)])
        self.assertEqual(r["previous"][0]["status"], "resolved")

    def test_no_pre_keeps_delta_rule(self):
        r = merge([res(0, [])], CFG, [P0()], None, {"a.cu"}, False)
        self.assertEqual(r["previous"][0]["status"], "unclear")


if __name__ == "__main__":
    unittest.main()
