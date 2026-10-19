import unittest
from collections import Counter

from review.fingerprint import (
    anchor_line, decode_seen, encode_seen, fp_line, fp_removed, norm, pack,
    unpack, whole_hash)

L = ["int k(int *p) {", "  return p[1];", "}"]


class FingerprintTest(unittest.TestCase):
    def test_norm_strips_trailing_only(self):
        self.assertEqual(norm("  x \r\n"), "  x")

    def test_window_changes_with_neighbor(self):
        self.assertNotEqual(fp_line(L, 2), fp_line(["int j(int *p) {"] + L[1:], 2))

    def test_window_same_when_shifted(self):
        self.assertEqual(fp_line(L, 2), fp_line(["// x"] * 10 + L, 12))

    def test_crlf_same_print(self):
        self.assertEqual(fp_line([s + "\r" for s in L], 2), fp_line(L, 2))

    def test_indent_matters(self):
        self.assertNotEqual(fp_line(["if x:", "    y()", ""], 2),
                            fp_line(["if x:", "y()", ""], 2))

    def test_edges_use_empty_neighbors(self):
        self.assertEqual(len(fp_line(L, 1)), 4)
        self.assertEqual(len(fp_line(L, 3)), 4)

    def test_removed_differs_from_added(self):
        self.assertNotEqual(fp_removed(L, 2, "  sync();"), fp_line(L, 2))

    def test_removed_depends_on_deletion_point(self):
        self.assertNotEqual(fp_removed(L, 2, "x"), fp_removed(L, 3, "x"))

    def test_anchor_line_at_eof(self):
        self.assertEqual((anchor_line(L, 2), anchor_line(L, 4), anchor_line([], 1)),
                         (2, 3, 1))

    def test_pack_roundtrip_keeps_counts(self):
        c = Counter({b"abcd": 2, b"wxyz": 1})
        self.assertEqual(unpack(pack(c)), c)

    def test_unpack_corrupt_is_empty(self):
        for bad in ["!!!", "YWJj", None, 5]:  # "YWJj" = 3 bytes
            self.assertEqual(unpack(bad), Counter())

    def test_encode_under_cap_keeps_lines(self):
        raw, files = encode_seen({"a": Counter({b"abcd": 1})})
        self.assertEqual((list(raw), files), (["a"], {}))

    def test_encode_omits_empty_counters(self):
        self.assertEqual(encode_seen({"a": Counter()}), ({}, {}))

    def test_encode_over_cap_moves_largest_file(self):
        big = Counter({i.to_bytes(4, "big"): 1 for i in range(3000)})  # ~16k chars
        small = Counter({b"abcd": 1})
        raw, files = encode_seen({"big": big, "small": small}, cap=1000)
        self.assertEqual((list(raw), list(files)), (["small"], ["big"]))
        self.assertEqual(decode_seen({"seen": raw, "seen_files": files})[1]["big"],
                         whole_hash(big))

    def test_encode_tie_moves_larger_path_first(self):
        c = Counter({b"abcd": 1})
        raw, files = encode_seen({"a": c, "b": c}, cap=8)
        self.assertEqual((list(raw), list(files)), (["a"], ["b"]))

    def test_decode_none_and_garbage(self):
        self.assertEqual(decode_seen(None), ({}, {}))
        self.assertEqual(decode_seen({"seen": {"a": 1}, "seen_files": "x"}), ({}, {}))

    def test_decode_skips_bad_entries_keeps_good(self):
        good = pack(Counter({b"abcd": 1}))
        st = {"seen": {"a": good, "b": "!!!", "c": 3},
              "seen_files": {"f": "YWJjZA==", "g": "YWJj", "h": 1}}
        seen, files = decode_seen(st)
        self.assertEqual(seen, {"a": Counter({b"abcd": 1})})
        self.assertEqual(files, {"f": b"abcd"})


if __name__ == "__main__":
    unittest.main()
