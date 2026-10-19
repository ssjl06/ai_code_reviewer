import unittest

from review.diff_split import HUNK_RE, parse_unified_diff, split_hunks
from review.hunks import diff_prints, near_hunks, select_file, split_hunk

HDR = "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
LINES = ["line_%03d = value(%03d)" % (i, i) for i in range(1, 61)]


def new_file(lines):
    return (HDR.replace("--- a/f.py", "new file mode 100644\n--- /dev/null")
            + "@@ -0,0 +1,%d @@\n" % len(lines) + "".join("+%s\n" % x for x in lines))


def counts(hunk):
    rows = hunk.rstrip("\n").split("\n")[1:]
    return (sum(1 for r in rows if r[:1] in " -"), sum(1 for r in rows if r[:1] in " +"))


class SplitHunkTest(unittest.TestCase):
    def test_small_hunk_untouched(self):
        h = "@@ -1,2 +1,3 @@\n a\n+x\n b\n"
        self.assertEqual(split_hunk(h, 1000), [h])

    def test_headers_recomputed_and_valid(self):
        h = "@@ -10,6 +10,6 @@ def f():\n a\n-b\n+B\n c\n-d\n+D\n e\n f\n"
        parts = split_hunk(h, 30)
        self.assertGreater(len(parts), 1)
        self.assertEqual("".join(p.split("\n", 1)[1] for p in parts), h.split("\n", 1)[1])
        old_pos, new_pos = 10, 10
        for p in parts:
            self.assertLessEqual(len(p), 30 + 40)
            head = p.split("\n", 1)[0]
            self.assertTrue(head.endswith(" @@ def f():"))
            old, new = counts(p)
            self.assertEqual(head.split(" @@")[0], "@@ -%d,%d +%d,%d" % (
                old_pos if old else old_pos - 1, old, new_pos, new))
            self.assertEqual(int(HUNK_RE.match(p).group(1)), new_pos)
            old_pos, new_pos = old_pos + old, new_pos + new

    def test_no_newline_marker_stays_with_its_line(self):
        h = "@@ -1,2 +1,2 @@\n a\n-b\n\\ No newline at end of file\n+c\n\\ No newline at end of file\n"
        for p in split_hunk(h, 8):
            rows = p.rstrip("\n").split("\n")
            self.assertFalse(rows[1].startswith("\\"))
        self.assertEqual(len(split_hunk(h, 8)), 3)

    def test_split_prints_equal_whole(self):
        text = new_file(LINES)
        f = parse_unified_diff(text)[0]
        whole = select_file(f, LINES, None, None)
        part = select_file(f, LINES, None, None, limit_chars=len(HDR) + 600)
        self.assertEqual(part["head"], whole["head"])
        self.assertEqual(part["added"], set(range(1, 61)))
        self.assertGreater(len(split_hunks(part["file"]["text"])[1]), 2)
        self.assertEqual(diff_prints(part["file"]["text"], {"f.py": LINES})["f.py"], whole["head"])

    def test_only_unseen_sub_hunks_kept(self):
        f = parse_unified_diff(new_file(LINES))[0]
        lim = len(HDR) + 900
        first = split_hunks(select_file(f, LINES, None, None, limit_chars=lim)["file"]["text"])[1]
        seen = diff_prints(HDR + first[0], {"f.py": LINES})["f.py"]
        s = select_file(f, LINES, seen, None, limit_chars=lim)
        kept = split_hunks(s["file"]["text"])[1]
        self.assertEqual(kept, first[1:])
        self.assertNotIn(1, s["added"])

    def test_near_hunks_picks_closest(self):
        text = HDR + "@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n@@ -40,3 +40,3 @@\n p\n-q\n+Q\n r\n"
        f = parse_unified_diff(text)[0]
        t = near_hunks(f, [35], None)
        self.assertEqual(split_hunks(t["text"])[1], ["@@ -40,3 +40,3 @@\n p\n-q\n+Q\n r\n"])
        t = near_hunks(f, [2, 41], None)
        self.assertEqual(len(split_hunks(t["text"])[1]), 2)
        self.assertIsNone(near_hunks(parse_unified_diff(HDR)[0], [1], None))


if __name__ == "__main__":
    unittest.main()
