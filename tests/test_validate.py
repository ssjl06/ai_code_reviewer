import unittest

from review.validate import normalize_output, normalize_state

GOOD = {"file": "a.c", "line": 3, "severity": "major", "confidence": 90, "category": "bug",
        "title": "t", "detail": "d"}


def out(*findings, **kw):
    o = {"summary": "s", "findings": list(findings), "previous_findings": [],
         "incomplete_reason": None}
    o.update(kw)
    return o


class NormalizeOutputTest(unittest.TestCase):
    def test_valid_passes_through(self):
        self.assertEqual(normalize_output(out(GOOD)), out(GOOD))

    def test_top_level_shape(self):
        self.assertIsNone(normalize_output("x"))
        self.assertIsNone(normalize_output({"findings": "none"}))
        self.assertIsNone(normalize_output({"summary": "s"}))

    def test_defaults(self):
        self.assertEqual(normalize_output({"findings": []}),
                         {"summary": "", "findings": [], "previous_findings": [],
                          "incomplete_reason": None})

    def test_missing_category_defaulted_and_coercions(self):
        raw = {"file": "a.c", "line": "4", "severity": "minor", "confidence": "90",
               "title": "x" * 100}
        f = normalize_output(out(raw))["findings"][0]
        self.assertEqual((f["category"], f["line"], f["confidence"], f["detail"]),
                         ("bug", 4, 90, ""))
        self.assertEqual(len(f["title"]), 80)
        f = normalize_output(out(dict(GOOD, category="weird", confidence=150)))["findings"][0]
        self.assertEqual((f["category"], f["confidence"]), ("bug", 100))

    def test_bad_findings_dropped(self):
        bad = [dict(GOOD, file=""), dict(GOOD, line=0), dict(GOOD, line="x"),
               dict(GOOD, severity="huge"), dict(GOOD, confidence=None), "str",
               {k: v for k, v in GOOD.items() if k != "file"}, dict(GOOD, line=True)]
        self.assertEqual(normalize_output(out(*bad, GOOD))["findings"], [GOOD])

    def test_optional_fields(self):
        f = normalize_output(out(dict(GOOD, suggestion="s", end_line=5)))["findings"][0]
        self.assertEqual((f["suggestion"], f["end_line"]), ("s", 5))
        f = normalize_output(out(dict(GOOD, suggestion=3, end_line=1)))["findings"][0]
        self.assertNotIn("suggestion", f)
        self.assertNotIn("end_line", f)

    def test_previous_and_reason(self):
        prev = [{"id": "a", "status": "open"}, {"id": 1, "status": "open"},
                {"id": "b", "status": "gone"}, "x", {"id": "c", "status": "resolved", "note": "n"}]
        o = normalize_output(out(previous_findings=prev, incomplete_reason=5, summary=None))
        self.assertEqual(o["previous_findings"], [{"id": "a", "status": "open"},
                                                  {"id": "c", "status": "resolved", "note": "n"}])
        self.assertIsNone(o["incomplete_reason"])
        self.assertEqual(o["summary"], "")
        self.assertEqual(normalize_output(out(previous_findings="x"))["previous_findings"], [])


class NormalizeStateTest(unittest.TestCase):
    def test_state(self):
        ok = {"id": "i", "file": "a.c", "line": 2, "title": "t", "severity": "major",
              "blocking": True}
        bad = [dict(ok, id=1), dict(ok, line="2"), dict(ok, blocking="yes"), "x",
               {k: v for k, v in ok.items() if k != "title"}]
        self.assertEqual(normalize_state({"sha": "abc", "findings": bad + [ok]}),
                         {"sha": "abc", "findings": [ok]})
        self.assertEqual(normalize_state({"sha": 1, "findings": "x"}), {"sha": "", "findings": []})
        self.assertIsNone(normalize_state(None))
        self.assertIsNone(normalize_state([1]))


if __name__ == "__main__":
    unittest.main()
