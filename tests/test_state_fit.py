import hashlib
import unittest
from collections import Counter

from review.fingerprint import decode_seen, encode_seen, whole_hash
from review.render import render_body, state_marker
from review.scope import next_state
from review.state_fit import MARKER_BUDGET, fit_state
from tests.test_render import META, REVIEW


def prints(path, n=5):
    return Counter(hashlib.sha1(("%s:%d" % (path, i)).encode()).digest()[:4] for i in range(n))


def finding(i, blocking):
    return {"id": "%08x" % i, "file": "src/module_%03d/component_file.py" % (i % 50),
            "line": i, "anchor": "0a0b0c0d", "title": "제목 " * 20 + str(i),
            "severity": "major" if blocking else "minor", "blocking": blocking,
            "confidence": 90, "detail": "d"}


def big_state():
    paths = ["src/package_%04d/some_module_name_x.py" % i for i in range(1000)]
    heads = {p: prints(p) for p in paths}
    scope = {"targets": [], "blobs": {}, "head_prints": heads, "prev_seen": heads}
    found = [finding(i, i % 40 == 0) for i in range(205)]
    review = {"findings": found, "previous": [], "complete": True}
    return next_state("h", review, scope, [], []), found


class FitStateTest(unittest.TestCase):
    def test_small_state_untouched(self):
        st = {"v": 2, "sha": "h", "complete": True, "seen": {"a": "AAAAAA=="},
              "seen_files": {}, "findings": [finding(1, False)]}
        self.assertEqual(fit_state(st), st)

    def test_moves_largest_files_first(self):
        seen = {"big": prints("big", 400), "small": prints("small", 2)}
        raw, files = encode_seen(seen)
        st = {"v": 2, "sha": "h", "complete": True, "seen": raw, "seen_files": files,
              "findings": []}
        out = fit_state(st, budget=len(state_marker(st)) - 100)
        self.assertEqual(set(out["seen"]), {"small"})
        self.assertEqual(decode_seen(out)[1], {"big": whole_hash(seen["big"])})

    def test_drops_seen_then_oldest_nonblocking(self):
        st = {"v": 2, "sha": "h", "complete": True, "seen": {"a": "AAAAAA=="},
              "seen_files": {"b": "AAAAAA=="},
              "findings": [finding(1, False), finding(2, True), finding(3, False)]}
        out = fit_state(st, budget=len(state_marker(dict(st, seen={}, seen_files={}))) - 10)
        self.assertEqual((out["seen"], out["seen_files"]), ({}, {}))
        self.assertEqual([f["id"] for f in out["findings"]], [finding(1, False)["id"],
                                                              finding(2, True)["id"]])
        out = fit_state(st, budget=10)
        self.assertEqual([f["blocking"] for f in out["findings"]], [True])

    def test_huge_pr_body_fits_and_keeps_blocking(self):
        st, found = big_state()
        self.assertLessEqual(len(state_marker(st)), MARKER_BUDGET)
        self.assertEqual((st["seen"], st["seen_files"]), ({}, {}))
        kept = {f["id"] for f in st["findings"]}
        self.assertTrue({f["id"] for f in found if f["blocking"]} <= kept)
        self.assertIn(found[1]["id"], kept)  # newest non-blocking kept, oldest dropped
        self.assertNotIn(found[-1]["id"], kept)
        body = render_body(REVIEW(), [], META, st)
        self.assertLess(len(body), 65536)


if __name__ == "__main__":
    unittest.main()
