import unittest
from collections import Counter

from review.diff_split import parse_unified_diff, split_hunks
from review.fingerprint import whole_hash
from review.hunks import diff_prints, scan_hunk, select_file

HEAD = ["a", "b", "NEW", "c", "d"]
HDR = "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
DIFF = HDR + "@@ -1,4 +1,5 @@\n a\n b\n+NEW\n c\n d\n"
DEL = HDR + "@@ -1,5 +1,4 @@\n a\n b\n-sync()\n c\n d\n"
TWO = HDR + "@@ -1,2 +1,3 @@\n a\n+X\n b\n@@ -9,2 +10,3 @@\n p\n+Y\n q\n"


def sel(diff, lines, seen=None, whole=None):
    return select_file(parse_unified_diff(diff)[0], lines, seen, whole)


class HunksTest(unittest.TestCase):
    def test_scan_added(self):
        ch = scan_hunk(split_hunks(DIFF)[1][0], HEAD)
        self.assertEqual([(c["kind"], c["line"]) for c in ch], [("+", 3)])

    def test_scan_deleted_anchor(self):
        ch = scan_hunk(split_hunks(DEL)[1][0], ["a", "b", "c", "d"])
        self.assertEqual([(c["kind"], c["line"]) for c in ch], [("-", 3)])

    def test_no_newline_marker_ignored(self):
        d = HDR + "@@ -1,1 +1,2 @@\n a\n+x\n\\ No newline at end of file\n"
        ch = scan_hunk(split_hunks(d)[1][0], ["a", "x"])
        self.assertEqual([(c["kind"], c["line"]) for c in ch], [("+", 2)])

    def test_first_review_keeps_everything(self):
        s = sel(DIFF, HEAD)
        self.assertEqual((s["added"], s["file"]["path"]), ({3}, "f.py"))

    def test_seen_hunk_dropped(self):
        head = sel(DIFF, HEAD)["head"]
        s = sel(DIFF, HEAD, head)
        self.assertIsNone(s["file"])
        self.assertEqual(s["added"], set())

    def test_deletion_only_hunk_is_target(self):
        s = sel(DEL, ["a", "b", "c", "d"], Counter())
        self.assertEqual((s["deleted"], s["added"]), ({3}, set()))

    def test_only_unseen_hunk_kept(self):
        lines = ["a", "X", "b"] + [""] * 6 + ["p", "Y", "q"]
        full = sel(TWO, lines)
        self.assertEqual(len(split_hunks(full["file"]["text"])[1]), 2)
        seen = Counter(list(full["head"].elements())[:1])
        s = sel(TWO, lines, seen)
        self.assertEqual(len(split_hunks(s["file"]["text"])[1]), 1)

    def test_duplicate_block_count_rises(self):
        one = sel(DIFF, HEAD)["head"]
        twice = HDR + ("@@ -1,2 +1,3 @@\n a\n b\n+NEW\n" * 2)
        lines = ["a", "b", "NEW"]
        h2 = sel(twice, lines)
        s = sel(twice, lines, Counter({k: 1 for k in h2["head"]}))
        self.assertIsNotNone(s["file"])
        self.assertEqual(len(split_hunks(s["file"]["text"])[1]), 2)
        self.assertTrue(one)

    def test_whole_hash_match_means_seen(self):
        head = sel(DIFF, HEAD)["head"]
        s = sel(DIFF, HEAD, None, whole_hash(head))
        self.assertIsNone(s["file"])
        s = sel(DIFF, HEAD, None, b"\0\0\0\0")
        self.assertIsNotNone(s["file"])

    def test_diff_prints_two_files(self):
        other = DIFF.replace("f.py", "g.py")
        out = diff_prints(DIFF + other + DIFF.replace("f.py", "h.py"),
                          {"f.py": HEAD, "g.py": HEAD})
        self.assertEqual(sorted(out), ["f.py", "g.py"])
        self.assertEqual(out["f.py"], sel(DIFF, HEAD)["head"])

    def test_crlf_blob_matches_diff(self):
        crlf = [x + "\r" for x in HEAD]
        d = DIFF.replace("\n a\n b\n+NEW\n c\n d\n", "\n a\r\n b\r\n+NEW\r\n c\r\n d\r\n")
        self.assertEqual(sel(d, crlf)["head"], sel(DIFF, HEAD)["head"])
        self.assertEqual(sel(d, crlf)["added"], {3})


if __name__ == "__main__":
    unittest.main()
