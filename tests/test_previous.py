import unittest

from review.fingerprint import fp_line
from review.previous import DELETED_NOTE, GONE_NOTE, _closest, annotate, anchor_of

LINES = ["int k(int *p) {", "  return p[1];", "}"]
A = fp_line(LINES, 2).hex()


def E(**kw):
    e = {"id": "f1", "file": "src/k.cu", "line": 2, "lo": 2, "hi": 2, "anchor": A, "title": "t",
         "severity": "major", "blocking": True, "status": "open", "orphan": False}
    e.update(kw)
    return e


class AnnotateTest(unittest.TestCase):
    """`annotate` replaces `relocate`: the issues are already at their head place, so it only
    describes them. Code moved to another file is never looked for."""

    def test_file_dropped_resolved(self):
        r = annotate([E()], set(), set())[0]
        self.assertEqual((r["pre"], r["note"], r["located"]), ("resolved", GONE_NOTE, False))

    def test_deleted_file_note(self):
        r = annotate([E()], set(), set(), {"src/k.cu"})[0]
        self.assertEqual((r["pre"], r["note"]), ("resolved", DELETED_NOTE))

    def test_the_same_code_in_another_file_is_not_followed(self):
        # was test_gone_file_anchor_found_elsewhere / test_reverted_file_not_searched: the
        # destination used to be searched by anchor, now only a link by the model follows it
        r = annotate([E()], {"a.py", "src/new.cu"}, set())[0]
        self.assertEqual((r["file"], r["pre"], r.get("moved")), ("src/k.cu", "resolved", None))

    def test_open_file_is_open_and_located(self):
        r = annotate([E()], {"src/k.cu"}, set())[0]
        self.assertEqual((r["pre"], r["located"], r["line"]), ("open", True, 2))

    def test_target_file_asks_model(self):
        r = annotate([E()], {"src/k.cu"}, {"src/k.cu"})[0]
        self.assertEqual((r["pre"], r["located"]), ("ask", True))

    def test_lost_issue_is_not_located(self):
        r = annotate([E()], {"src/k.cu"}, {"src/k.cu"}, lost={"f1"})[0]
        self.assertEqual((r["pre"], r["located"], r["line"]), ("ask", False, 2))
        r = annotate([E()], {"src/k.cu"}, set(), lost={"f1"})[0]
        self.assertEqual((r["pre"], r["located"]), ("open", False))

    def test_orphan_is_pre_orphan_even_in_a_target(self):
        r = annotate([E(orphan=True)], {"src/k.cu"}, {"src/k.cu"})[0]
        self.assertEqual((r["pre"], r["located"]), ("orphan", True))

    def test_weak_tracking_does_not_resolve_a_missing_file(self):
        # without the previous head a left-the-PR file cannot be told from a renamed one
        r = annotate([E()], set(), set(), {"src/k.cu"}, weak=True)[0]
        self.assertEqual((r["pre"], r["located"], r.get("note")), ("ask", False, None))
        r = annotate([E()], {"src/k.cu"}, set(), weak=True)[0]
        self.assertEqual((r["pre"], r["located"]), ("open", True))

    def test_orphan_whose_file_left_the_pr_is_resolved(self):
        r = annotate([E(orphan=True)], set(), set())[0]
        # resolved with the file, so a `same_as` link must not swallow a finding for it
        self.assertEqual((r["pre"], r["orphan"]), ("resolved", False))

    def test_a_renamed_file_is_just_a_file(self):
        # was test_rename_carries_finding: `ledger.advance` already moved the issue
        r = annotate([E(file="src/k.cu")], {"src/k.cu"}, set())[0]
        self.assertEqual((r["file"], r["pre"], r["located"]), ("src/k.cu", "open", True))

    def test_input_not_mutated(self):
        e = E()
        annotate([e], set(), set())
        self.assertNotIn("pre", e)


class ClosestTest(unittest.TestCase):
    def test_shifted_line_found(self):
        # was test_shifted_line_relocated
        self.assertEqual(_closest(["//"] * 10 + LINES, A, 2), 12)

    def test_changed_anchor_not_found(self):
        self.assertIsNone(_closest(["int k(int *p) {", "  return p[0];", "}"], A, 2))

    def test_closest_duplicate_wins(self):
        self.assertEqual(_closest(LINES + ["//"] * 4 + LINES, A, 8), 9)  # anchor at 2 and 9

    def test_tie_takes_smaller(self):
        self.assertEqual(_closest(LINES + ["//"] * 3 + LINES, A, 5), 2)  # anchor at 2 and 8

    def test_empty_anchor_never_matches(self):
        self.assertIsNone(_closest(LINES, "", 2))


class AnchorOfTest(unittest.TestCase):
    def test_anchor_of(self):
        fn = anchor_of({"src/k.cu": LINES})
        self.assertEqual((fn("src/k.cu", 2), fn("x", 1)), (A, ""))


if __name__ == "__main__":
    unittest.main()
