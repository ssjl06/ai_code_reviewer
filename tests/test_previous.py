import unittest

from review.fingerprint import fp_line
from review.previous import anchor_of, relocate

LINES = ["int k(int *p) {", "  return p[1];", "}"]
A = fp_line(LINES, 2).hex()


def E(**kw):
    e = {"id": "f1", "file": "src/k.cu", "line": 2, "anchor": A, "title": "t",
         "severity": "major", "blocking": True}
    e.update(kw)
    return e


class RelocateTest(unittest.TestCase):
    def test_file_dropped_resolved(self):
        r = relocate([E()], set(), {}, set(), {})[0]
        self.assertEqual((r["pre"], r["note"], r["located"]),
                         ("resolved", "파일이 PR에서 빠짐", False))

    def test_deleted_file_note(self):
        r = relocate([E()], set(), {}, set(), {}, {"src/k.cu"})[0]
        self.assertEqual((r["pre"], r["note"]), ("resolved", "파일이 삭제됨"))

    def test_gone_file_anchor_found_elsewhere(self):
        blobs = {"a.py": ["x"], "src/new.cu": ["//"] + LINES}
        r = relocate([E(file="src/mid.cu")], {"a.py", "src/new.cu"}, blobs, set(), {},
                     search=["a.py", "src/new.cu"])[0]
        self.assertEqual((r["file"], r["line"], r["located"], r["pre"]),
                         ("src/new.cu", 3, True, "open"))

    def test_reverted_file_not_searched(self):
        blobs = {"src/k.cu": LINES, "src/new.cu": LINES}
        r = relocate([E()], {"src/new.cu"}, blobs, set(), {}, search=["src/new.cu"])[0]
        self.assertEqual((r["file"], r["pre"], r["note"]), ("src/k.cu", "resolved", "파일이 PR에서 빠짐"))

    def test_shifted_line_relocated(self):
        blobs = {"src/k.cu": ["//"] * 10 + LINES}
        r = relocate([E()], {"src/k.cu"}, blobs, set(), {})[0]
        self.assertEqual((r["line"], r["located"], r["pre"]), (12, True, "open"))

    def test_target_file_asks_model(self):
        r = relocate([E()], {"src/k.cu"}, {"src/k.cu": LINES}, {"src/k.cu"}, {})[0]
        self.assertEqual((r["pre"], r["located"]), ("ask", True))

    def test_changed_anchor_not_located(self):
        blobs = {"src/k.cu": ["int k(int *p) {", "  return p[0];", "}"]}
        r = relocate([E()], {"src/k.cu"}, blobs, {"src/k.cu"}, {})[0]
        self.assertEqual((r["located"], r["line"]), (False, 2))

    def test_closest_duplicate_wins(self):
        blobs = {"src/k.cu": LINES + ["//"] * 4 + LINES}  # anchor at 2 and 9
        r = relocate([E(line=8)], {"src/k.cu"}, blobs, set(), {})[0]
        self.assertEqual(r["line"], 9)

    def test_tie_takes_smaller(self):
        blobs = {"src/k.cu": LINES + ["//"] * 3 + LINES}  # anchor at 2 and 8
        r = relocate([E(line=5)], {"src/k.cu"}, blobs, set(), {})[0]
        self.assertEqual(r["line"], 2)

    def test_empty_anchor_never_matches(self):
        r = relocate([E(anchor="")], {"src/k.cu"}, {"src/k.cu": LINES}, set(), {})[0]
        self.assertEqual((r["located"], r["pre"]), (False, "open"))

    def test_input_not_mutated(self):
        e = E()
        relocate([e], set(), {}, set(), {})
        self.assertNotIn("pre", e)

    def test_rename_carries_finding(self):
        r = relocate([E(file="old.cu")], {"src/k.cu"}, {"src/k.cu": LINES}, set(),
                     {"old.cu": "src/k.cu"})[0]
        self.assertEqual((r["file"], r["pre"], r["located"]), ("src/k.cu", "open", True))

    def test_anchor_of(self):
        fn = anchor_of({"src/k.cu": LINES})
        self.assertEqual((fn("src/k.cu", 2), fn("x", 1)), (A, ""))


if __name__ == "__main__":
    unittest.main()
