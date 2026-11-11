import unittest

from review.mention.ledger import apply_changes, ledger_event, marker_state
from review.state_fit import MARKER_BUDGET
from review.render import state_marker
from review.validate import normalize_state


def issue(n, status="open", blocking=False):
    return {"id": "%08x" % n, "file": "src/f%d.py" % (n % 7), "line": 10 + n, "lo": 10 + n,
            "hi": 10 + n, "status": status, "severity": "major" if blocking else "minor",
            "blocking": blocking, "anchor": "0a0b0c0d", "title": "제목 %d" % n,
            "excerpt": "설명 " * 20, "orphan": False}


def base(issues=None, unclear=(), approvable=True):
    return {"v": 3, "sha": "h1", "complete": True, "seen": {}, "seen_files": {"a.py": "QQ=="},
            "seen_hunks": {"a.py": "x"}, "issues": issues if issues is not None else [
                issue(1), issue(2), issue(3, "resolved"), issue(4, "waived")],
            "recheck_pending": ["r1"], "approvable": approvable,
            "unclear": None if unclear is None else list(unclear), "cut": {"a.py": "p"}}


RECEIPT = {"k": "r:123", "w": [], "n": [], "up": []}


class ApplyChangesTest(unittest.TestCase):
    def test_waive_add_upgrade(self):
        s = base()
        new, done = apply_changes(s, {"w": [issue(1)["id"]], "n": [issue(9, blocking=True)],
                                      "up": [issue(2)["id"]]})
        by = {i["id"]: i for i in new["issues"]}
        self.assertEqual(by[issue(1)["id"]]["status"], "waived")
        self.assertTrue(by[issue(2)["id"]]["blocking"])
        self.assertEqual(new["issues"][0]["id"], issue(9)["id"])
        self.assertEqual(new["issues"][0]["status"], "open")
        self.assertEqual(done, {"w": [issue(1)["id"]], "n": [issue(9)["id"]],
                                "up": [issue(2)["id"]]})
        self.assertEqual(s["issues"][0]["status"], "open")  # input untouched

    def test_closed_not_reopened_or_re_added(self):
        s = base()
        new, done = apply_changes(s, {"w": [issue(3)["id"], issue(4)["id"]],
                                      "n": [issue(3), issue(1)],
                                      "up": [issue(3)["id"]]})
        self.assertEqual(done, {"w": [], "n": [], "up": []})
        self.assertEqual(new["issues"], s["issues"])

    def test_unknown_ids_ignored(self):
        new, done = apply_changes(base(), {"w": ["zz"], "n": [], "up": ["yy"]})
        self.assertEqual(done, {"w": [], "n": [], "up": []})

    def test_idempotent(self):
        ch = {"w": [issue(1)["id"]], "n": [issue(9, blocking=True)], "up": [issue(2)["id"]]}
        new, _ = apply_changes(base(), ch)
        again, done = apply_changes(new, ch)
        self.assertEqual(done, {"w": [], "n": [], "up": []})
        self.assertEqual(again, new)

    def test_coverage_untouched(self):
        s = dict(base(), waived_now=3)
        new, _ = apply_changes(s, {"w": [issue(1)["id"]], "n": [issue(9)], "up": []})
        for k in s:
            if k not in ("issues", "waived_now"):
                self.assertEqual(new[k], s[k], k)
        self.assertNotIn("waived_now", new)


class MarkerStateTest(unittest.TestCase):
    def test_marker_state_round_trip(self):
        s = base(unclear=[issue(2)["id"]])
        ms = marker_state(s, dict(RECEIPT, w=["x"]))
        back = normalize_state(ms)
        self.assertEqual(back["issues"], s["issues"])
        for k in ("sha", "complete", "seen_files", "seen_hunks", "approvable", "unclear",
                  "cut", "recheck_pending"):
            self.assertEqual(back[k], s[k], k)
        self.assertEqual(back["m"], dict(RECEIPT, w=["x"]))

    def test_dropped_closed_kept(self):
        back = normalize_state(marker_state(dict(base(), dropped_closed=5), RECEIPT))
        self.assertEqual(back["dropped_closed"], 5)

    def test_old_marker_stays_old(self):
        back = normalize_state(marker_state(base(unclear=None), RECEIPT))
        self.assertIsNone(back["unclear"])

    def test_marker_state_fits_budget(self):
        issues = [issue(i, blocking=i % 3 == 0) for i in range(150)]
        issues += [issue(1000 + i, "waived") for i in range(600)]
        ms = marker_state(base(issues), RECEIPT)
        self.assertLessEqual(len(state_marker(ms)), MARKER_BUDGET)
        back = normalize_state(ms)
        open_ids = {i["id"] for i in issues if i["status"] == "open"}
        self.assertEqual({i["id"] for i in back["issues"] if i["status"] == "open"}, open_ids)


class LedgerEventTest(unittest.TestCase):
    def ev(self, issues, current=True, added=False, **kw):
        return ledger_event(base(issues, **kw), current, added)

    def test_ledger_event(self):
        self.assertEqual(self.ev([issue(1, blocking=True)]), "REQUEST_CHANGES")
        self.assertEqual(self.ev([issue(1)]), "APPROVE")
        self.assertEqual(self.ev([issue(1)], approvable=False), "COMMENT")
        self.assertEqual(self.ev([issue(1)], current=False), "COMMENT")
        self.assertEqual(self.ev([issue(1)], unclear=[issue(1)["id"]]), "COMMENT")
        orphan = dict(issue(1, blocking=True), orphan=True)
        self.assertEqual(self.ev([orphan]), "REQUEST_CHANGES")
        self.assertEqual(self.ev([issue(1, blocking=True)], unclear=[issue(1)["id"]]), "COMMENT")
        self.assertEqual(self.ev([issue(1, "waived", blocking=True)]), "APPROVE")

    def test_added_blocking_with_unclear_list(self):
        """On a current state the ledger decides; on a stale one only the mention's own."""
        self.assertEqual(self.ev([issue(1)], added=True), "APPROVE")
        self.assertEqual(self.ev([issue(1)], current=False, added=True), "REQUEST_CHANGES")

    def test_stale_blockers_do_not_request_changes(self):
        """A state not current for the head: its open blockers may be fixed already."""
        iss = [issue(1, blocking=True), issue(2), issue(3, "waived")]
        self.assertEqual(self.ev(iss, current=False), "COMMENT")
        self.assertEqual(self.ev(iss, current=False, added=True), "REQUEST_CHANGES")
        self.assertEqual(self.ev(iss, current=True), "REQUEST_CHANGES")

    def test_old_marker(self):
        iss = [issue(1, blocking=True)]
        self.assertEqual(self.ev(iss, unclear=None), "COMMENT")
        self.assertEqual(self.ev(iss, unclear=None, added=True), "REQUEST_CHANGES")
        self.assertEqual(self.ev([issue(1)], unclear=None), "COMMENT")


if __name__ == "__main__":
    unittest.main()
