import unittest

from review.validate import compact_issue, expand_issue, normalize_output, normalize_state

GOOD = {"file": "a.c", "line": 3, "severity": "major", "confidence": 90, "category": "bug",
        "title": "t", "detail": "d"}


def out(*findings, **kw):
    o = {"summary": "s", "findings": list(findings), "previous_findings": [],
         "incomplete_reason": None, "diff_reviewed": True}
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
                          "incomplete_reason": None, "diff_reviewed": False,
                          "diff_reviewed_invalid": True})

    def test_diff_reviewed(self):
        self.assertIs(normalize_output(out(diff_reviewed=False))["diff_reviewed"], False)
        self.assertIs(normalize_output(out(diff_reviewed=True))["diff_reviewed"], True)
        self.assertNotIn("diff_reviewed_invalid", normalize_output(out(diff_reviewed=False)))
        self.assertNotIn("diff_reviewed_invalid", normalize_output(out(diff_reviewed=True)))

    def test_only_boolean_true_counts_as_reviewed(self):
        for bad in (None, "false", "true", 1, 0, [], {}):
            o = normalize_output(out(diff_reviewed=bad))
            self.assertIs(o["diff_reviewed"], False, bad)
            self.assertIs(o["diff_reviewed_invalid"], True, bad)
        missing = normalize_output({"findings": []})
        self.assertIs(missing["diff_reviewed"], False)
        self.assertIs(missing["diff_reviewed_invalid"], True)

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

    def test_same_as_kept_only_as_a_string(self):
        f = normalize_output(out(dict(GOOD, same_as="i1")))["findings"][0]
        self.assertEqual(f["same_as"], "i1")
        f = normalize_output(out(dict(GOOD, same_as=3)))["findings"][0]
        self.assertNotIn("same_as", f)

    def test_previous_and_reason(self):
        prev = [{"id": "a", "status": "open"}, {"id": 1, "status": "open"},
                {"id": "b", "status": "gone"}, "x", {"id": "c", "status": "resolved", "note": "n"}]
        o = normalize_output(out(previous_findings=prev, incomplete_reason=5, summary=None))
        self.assertEqual(o["previous_findings"], [{"id": "a", "status": "open"},
                                                  {"id": "c", "status": "resolved", "note": "n"}])
        self.assertIsNone(o["incomplete_reason"])
        self.assertEqual(o["summary"], "")
        self.assertEqual(normalize_output(out(previous_findings="x"))["previous_findings"], [])


V2_FINDING = {"id": "abcd1234", "file": "a.cc", "line": 10, "anchor": "0a0b0c0d", "title": "t",
              "severity": "major", "blocking": True}
V2_STATE = {"v": 2, "sha": "abc", "complete": True, "seen": {"a.c": "AQIDBA=="},
            "seen_files": {"b.c": "AQIDBA=="}, "seen_hunks": {"a.c": "BQYHCA=="},
            "findings": [V2_FINDING]}
V3_ISSUE = {"id": "00000001", "file": "a.cc", "line": 20, "lo": 19, "hi": 21, "status": "open",
            "severity": "minor", "blocking": False, "anchor": "0a0b0c0d", "title": "t",
            "excerpt": "e", "orphan": False}
V3_STATE = {"v": 3, "sha": "abc", "complete": True, "seen": {"a.c": "AQIDBA=="},
            "seen_files": {}, "seen_hunks": {}, "issues": [V3_ISSUE], "closed": {},
            "recheck_pending": []}


class NormalizeStateTest(unittest.TestCase):
    def test_v2_marker_migrates_open_issues(self):
        st = normalize_state(V2_STATE)
        self.assertEqual(st["v"], 3)
        self.assertEqual([(i["id"], i["status"], i["lo"], i["hi"]) for i in st["issues"]],
                         [("abcd1234", "open", 10, 10)])
        self.assertEqual(st["seen"], V2_STATE["seen"])
        self.assertEqual((st["seen_files"], st["seen_hunks"], st["recheck_pending"]),
                         (V2_STATE["seen_files"], V2_STATE["seen_hunks"], []))
        self.assertNotIn("findings", st)

    def test_v2_without_seen_hunks_and_with_bad_entries(self):
        ok = V2_FINDING
        bad = [dict(ok, id=1), dict(ok, line="2"), dict(ok, blocking="yes"), "x",
               {k: v for k, v in ok.items() if k != "title"}]
        st = {"v": 2, "sha": "abc", "findings": bad + [ok, dict(ok, id="n2", anchor=5)],
              "seen": {"a.c": 1, "b.c": "x", 3: "y"}, "seen_files": "z",
              "seen_hunks": {"a.c": 2, "c.c": "y"}}
        out = normalize_state(st)
        self.assertEqual([(i["id"], i["anchor"]) for i in out["issues"]],
                         [("abcd1234", "0a0b0c0d"), ("n2", "")])
        self.assertEqual((out["seen"], out["seen_files"], out["seen_hunks"], out["complete"]),
                         ({"b.c": "x"}, {}, {"c.c": "y"}, False))
        self.assertEqual(normalize_state({"v": 2, "sha": 1, "complete": "yes", "findings": "x"}),
                         {"v": 3, "sha": "", "complete": False, "seen": {}, "seen_files": {},
                          "seen_hunks": {}, "issues": [], "recheck_pending": []})

    def test_v3_round_trip(self):
        out = normalize_state(V3_STATE)
        self.assertEqual(out, {k: v for k, v in V3_STATE.items() if k != "closed"}
                         | {"seen_hunks": {}})

    def test_cut_prints_are_kept(self):
        st = dict(V3_STATE, cut={"a.js": "AQIDBA==", 3: "x", "b.js": 1})
        self.assertEqual(normalize_state(st)["cut"], {"a.js": "AQIDBA=="})
        self.assertNotIn("cut", normalize_state(dict(V3_STATE, cut="x")))

    def test_v1_state_means_no_state(self):
        self.assertIsNone(normalize_state({"v": 1}))
        self.assertIsNone(normalize_state({"sha": "abc", "findings": [V2_FINDING]}))
        self.assertIsNone(normalize_state({"v": "3", "sha": "abc", "issues": []}))
        self.assertIsNone(normalize_state(None))
        self.assertIsNone(normalize_state([1]))

    def test_compact_closed_round_trips(self):
        st = normalize_state(dict(V3_STATE, closed={"a.cc": "abcd1234:10:9:11:wm"}))
        i = st["issues"][-1]
        self.assertEqual((i["id"], i["status"], i["severity"], i["lo"]),
                         ("abcd1234", "waived", "major", 9))
        self.assertEqual((i["file"], i["line"], i["hi"], i["title"], i["blocking"], i["orphan"]),
                         ("a.cc", 10, 11, "", False, False))

    def test_compact_orphan_and_every_severity_round_trip(self):
        st = normalize_state(dict(V3_STATE, closed={
            "a.cc": "00000002:10:0:0:rc 00000003:11:5:12:rn 00000004:12:12:12:wi bad 0:1:2"}))
        got = [(i["id"], i["orphan"], i["status"], i["severity"], i["lo"], i["hi"])
               for i in st["issues"][1:]]
        self.assertEqual(got, [("00000002", True, "resolved", "critical", 10, 10),
                               ("00000003", False, "resolved", "minor", 5, 12),
                               ("00000004", False, "waived", "info", 12, 12)])

    def test_via_survives_compaction(self):
        i = normalize_state(dict(V3_STATE, issues=[dict(V3_ISSUE, via="00000001")]))["issues"][0]
        self.assertEqual(i["via"], "00000001")
        self.assertNotIn("via", normalize_state(dict(V3_STATE, issues=[dict(V3_ISSUE, via=3)]))
                         ["issues"][0])
        closed = dict(i, status="resolved")
        tok = compact_issue(closed)
        self.assertEqual(tok.split(":")[-1], "00000001")
        back = expand_issue("a.cc", tok)
        self.assertEqual((back["id"], back["via"], back["status"]),
                         (i["id"], "00000001", "resolved"))
        five = expand_issue("a.cc", compact_issue({k: v for k, v in closed.items() if k != "via"}))
        self.assertEqual((five["id"], "via" in five), (i["id"], False))
        self.assertIsNone(expand_issue("a.cc", tok + ":x"))
        self.assertIsNone(expand_issue("a.cc", compact_issue(dict(closed, via="")) + ":"))

    def test_a_malformed_issue_is_dropped(self):
        self.assertEqual(normalize_state(dict(V3_STATE, issues=[{"id": 1}]))["issues"], [])
        bad = [dict(V3_ISSUE, status="unclear"), dict(V3_ISSUE, line="2"), "x",
               dict(V3_ISSUE, blocking=1), dict(V3_ISSUE, id=1)]
        self.assertEqual(normalize_state(dict(V3_STATE, issues=bad))["issues"], [])

    def test_missing_optional_issue_fields_get_defaults(self):
        slim = {k: V3_ISSUE[k] for k in ("id", "file", "line", "status", "severity", "blocking")}
        i = normalize_state(dict(V3_STATE, issues=[slim]))["issues"][0]
        self.assertEqual((i["lo"], i["hi"], i["anchor"], i["title"], i["excerpt"], i["orphan"]),
                         (20, 20, "", "", "", False))

    def test_recheck_pending_and_dropped_closed(self):
        st = normalize_state(dict(V3_STATE, recheck_pending=["a", 1, "b"], dropped_closed=4))
        self.assertEqual((st["recheck_pending"], st["dropped_closed"]), (["a", "b"], 4))
        self.assertNotIn("dropped_closed", normalize_state(dict(V3_STATE, dropped_closed="x")))


if __name__ == "__main__":
    unittest.main()
