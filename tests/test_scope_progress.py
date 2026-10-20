"""Capped and oversized reviews make progress on the same head (positional hunk keys)."""
import math
import unittest

from review.config import load_config
from review.hunks import _hunks
from review.diff_split import parse_unified_diff
from review.gitutil import diff_text
from tests.test_scope_regress import Repo, hunk_heads, review

CFG = load_config()


class CappedProgressTest(unittest.TestCase):
    def _loop(self, text, cfg):
        r = Repo(self)
        base = r.commit({"README.md": "r\n"})
        head = r.commit({"big.c": text})
        f = parse_unified_diff(diff_text(r.d, base, head, merge_base=True))[0]
        total = len(_hunks(f, int(cfg["chunk_max_tokens"] * 3.5))[1])
        runs = math.ceil(total / cfg["max_chunks"]) + 1
        prev, last, n = None, None, 0
        for n in range(1, runs + 1):
            s, chunks, rv, st = review(r, base, head, prev, cfg)
            if s["mode"] == "unchanged":
                break
            first = hunk_heads(chunks)[0]
            self.assertNotEqual(first, last, "run %d repeats the previous run" % n)
            last, prev = first, st
        else:
            self.fail("not unchanged after %d runs" % runs)
        self.assertGreater(n, 2)
        self.assertTrue(prev["complete"])

    def test_identical_lines_capped(self):
        self._loop("  0, 0, 0, 0,\n" * 1000, dict(CFG, max_chunks=1, chunk_max_tokens=300))

    def test_oversized_unique_lines(self):
        self._loop("".join("int v%d = %d;\n" % (i, i) for i in range(30000)), CFG)


if __name__ == "__main__":
    unittest.main()
