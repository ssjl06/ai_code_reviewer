import json
import os
import tempfile
import unittest
from collections import Counter

from review.fingerprint import decode_seen
from review.cli_util import parse_overrides, read_state_file
from review.scope import next_state

NEW = {"id": "n1", "file": "a", "line": 1, "anchor": "0a0b0c0d", "title": "t",
       "severity": "major", "blocking": True, "confidence": 90, "detail": "d"}
P1 = b"\x01\x02\x03\x04"


def empty_scope(**kw):
    s = {"targets": [], "blobs": {}, "head_prints": {}, "prev_seen": {}}
    s.update(kw)
    return s


class NextStateTest(unittest.TestCase):
    def test_next_state_v2_shape(self):
        prev = [dict(NEW, id="p1", status="open", pre="ask", located=True),
                dict(NEW, id="p2", status="resolved"),
                dict({k: v for k, v in NEW.items() if k != "anchor"}, id="p3",
                     status="unclear", note="x")]
        review = {"findings": [NEW], "previous": prev, "complete": True}
        scope = empty_scope(head_prints={"a": Counter([P1])}, prev_seen={"a": Counter([P1])})
        st = next_state("h", review, scope, [], [])
        self.assertEqual(set(st), {"v", "sha", "complete", "seen", "seen_files", "seen_hunks", "findings"})
        self.assertEqual((st["v"], st["sha"], st["complete"]), (2, "h", True))
        self.assertEqual([f["id"] for f in st["findings"]], ["n1", "p1", "p3"])
        self.assertEqual(set(st["findings"][1]), {"id", "file", "line", "anchor", "title",
                                                  "severity", "blocking"})
        self.assertEqual(st["findings"][2]["anchor"], "")
        self.assertEqual(decode_seen(st)[0], {"a": Counter([P1])})
        json.dumps(st)

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
            self.assertEqual(read_state_file(p), {"v": 2, "sha": "x", "complete": True,
                                                  "seen": {}, "seen_files": {}, "seen_hunks": {},
                                                  "findings": []})


if __name__ == "__main__":
    unittest.main()
