import json
import os
import tempfile
import unittest
from collections import Counter

from review.fingerprint import decode_seen
from review.cli_util import parse_overrides, read_state_file
from review.scope import next_state
from review.validate import normalize_state

NEW = {"id": "n1", "file": "a", "line": 1, "anchor": "0a0b0c0d", "title": "t",
       "severity": "major", "blocking": True, "confidence": 90, "detail": "d"}
P1 = b"\x01\x02\x03\x04"


def empty_scope(**kw):
    s = {"targets": [], "blobs": {}, "head_prints": {}, "prev_seen": {}, "closed": []}
    s.update(kw)
    return s


def issue(id_, status="open", **kw):
    e = {"id": id_, "file": "a", "line": 5, "lo": 4, "hi": 6, "status": status,
         "severity": "major", "blocking": True, "anchor": "0a0b0c0d", "title": "t",
         "excerpt": "", "orphan": False}
    e.update(kw)
    return e


class NextStateTest(unittest.TestCase):
    def test_next_state_v3_shape(self):
        prev = [dict(issue("p1"), pre="ask", located=True),
                issue("p2", "resolved"),
                dict(issue("p3", "unclear", anchor=""), note="x")]
        review = {"findings": [NEW], "previous": prev, "complete": True}
        scope = empty_scope(head_prints={"a": Counter([P1])}, prev_seen={"a": Counter([P1])},
                            closed=[issue("c1", "waived")])
        st = next_state("h", review, scope, [], [])
        self.assertEqual(set(st), {"v", "sha", "complete", "seen", "seen_files", "seen_hunks",
                                   "issues", "closed", "recheck_pending"})
        self.assertEqual((st["v"], st["sha"], st["complete"], st["closed"], st["recheck_pending"]),
                         (3, "h", True, {}, []))
        self.assertEqual([(i["id"], i["status"]) for i in st["issues"]],
                         [("n1", "open"), ("p1", "open"), ("p3", "open"),
                          ("p2", "resolved"), ("c1", "waived")])
        self.assertEqual(set(st["issues"][1]), {"id", "file", "line", "lo", "hi", "status",
                                                "severity", "blocking", "anchor", "title"})
        self.assertEqual(st["issues"][2]["anchor"], "")
        self.assertEqual(decode_seen(st)[0], {"a": Counter([P1])})
        json.dumps(st)

    def test_new_finding_becomes_an_open_issue(self):
        review = {"findings": [NEW], "previous": [], "complete": True}
        st = next_state("h", review, empty_scope(), [], [])
        i = st["issues"][0]
        self.assertEqual((i["id"], i["status"], i["line"], i["lo"], i["hi"], i["blocking"]),
                         ("n1", "open", 1, 1, 1, True))
        self.assertEqual((i["title"], i["excerpt"], i["anchor"]), ("t", "d", "0a0b0c0d"))
        self.assertNotIn("orphan", i)

    def test_closed_issue_stays_in_the_next_state(self):
        review = {"findings": [], "previous": [], "complete": True}
        st = next_state("h", review, empty_scope(closed=[issue("w1", "waived")]), [], [])
        self.assertEqual([(i["id"], i["status"]) for i in normalize_state(st)["issues"]],
                         [("w1", "waived")])

    def test_unclear_is_stored_as_open_and_a_file_left_behind_as_orphan(self):
        prev = [issue("u1", "unclear"), dict(issue("g1", "resolved"), pre="resolved"),
                dict(issue("o1", "resolved", orphan=True), pre="orphan")]
        st = next_state("h", {"findings": [], "previous": prev, "complete": True},
                        empty_scope(), [], [])
        self.assertEqual([(i["id"], i["status"], i.get("orphan", False)) for i in st["issues"]],
                         [("u1", "open", False), ("g1", "resolved", True), ("o1", "resolved", True)])

    def test_a_link_gives_a_closed_orphan_its_place_and_it_stays_closed(self):
        closed = [issue("c1", "waived", orphan=True), issue("c2", "resolved", orphan=True),
                  issue("c3", "resolved", orphan=True)]
        review = {"findings": [], "previous": [], "complete": True,
                  "links": [{"id": "c1", "file": "b.cc", "line": 50}],
                  "duplicates": [{"file": "d.cc", "line": 9, "dup_of": "c2"}]}
        st = next_state("h", review, empty_scope(closed=closed), [], [])
        got = {i["id"]: (i["status"], i["file"], i["line"], i["lo"], i["hi"], i.get("orphan", False))
               for i in st["issues"]}
        self.assertEqual(got, {"c1": ("waived", "b.cc", 50, 50, 50, False),
                               "c2": ("resolved", "d.cc", 9, 9, 9, False),
                               "c3": ("resolved", "a", 5, 4, 6, True)})

    def test_a_link_to_a_closed_issue_with_a_region_changes_nothing(self):
        review = {"findings": [], "previous": [], "complete": True,
                  "links": [{"id": "c1", "file": "b.cc", "line": 50}]}
        st = next_state("h", review, empty_scope(closed=[issue("c1", "waived")]), [], [])
        self.assertEqual((st["issues"][0]["file"], st["issues"][0]["line"]), ("a", 5))

    def test_recheck_pending_and_dropped_closed_are_carried(self):
        review = {"findings": [], "previous": [], "complete": True, "recheck_pending": ["p1"]}
        st = next_state("h", review, empty_scope(dropped_closed=4), [], [])
        self.assertEqual((st["recheck_pending"], st["dropped_closed"]), (["p1"], 4))
        st = next_state("h", {"findings": [], "previous": [], "complete": True},
                        empty_scope(), [], [])
        self.assertEqual((st["recheck_pending"], "dropped_closed" in st), ([], False))

    def test_incomplete_keeps_head_sha(self):
        review = {"findings": [], "previous": [], "complete": False, "skipped": ["x"]}
        st = next_state("h", review, empty_scope(), [], [])
        self.assertEqual((st["sha"], st["complete"], st["seen"], st["seen_files"]),
                         ("h", False, {}, {}))


class HelpersTest(unittest.TestCase):
    def test_parse_overrides(self):
        got = parse_overrides(["max_chunks=2", "claude_bin=/a/b", "exclude_globs=[\"x\"]"])
        self.assertEqual(got, {"max_chunks": 2, "claude_bin": "/a/b", "exclude_globs": ["x"]})
        with self.assertRaises(ValueError):
            parse_overrides(["novalue"])

    def test_read_state_file(self):
        self.assertIsNone(read_state_file(None))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            with open(p, "w") as f:
                json.dump({"sha": "x", "findings": []}, f)
            self.assertIsNone(read_state_file(p))  # v1 state = no previous state
            with open(p, "w") as f:
                json.dump({"v": 2, "sha": "x", "complete": True, "findings": []}, f)
            self.assertEqual(read_state_file(p), {"v": 3, "sha": "x", "complete": True,
                                                  "seen": {}, "seen_files": {}, "seen_hunks": {},
                                                  "issues": [], "recheck_pending": []})


if __name__ == "__main__":
    unittest.main()
