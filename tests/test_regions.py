import subprocess
import unittest

from review import gitutil
from review.regions import map_line, map_region, parse_u0
from tests.test_scope_regress import Repo

RENAME_DIFF = """diff --git a/old.cc b/new.cc
similarity index 80%
rename from old.cc
rename to new.cc
index 1111111..2222222 100644
--- a/old.cc
+++ b/new.cc
@@ -1,0 +2,2 @@ int a;
+int b;
+int c;
"""

DELETE_DIFF = """diff --git a/gone.cc b/gone.cc
deleted file mode 100644
index 1111111..0000000
--- a/gone.cc
+++ /dev/null
@@ -1,2 +0,0 @@
-x
-y
"""


class MapTest(unittest.TestCase):
    def test_line_below_an_insertion_shifts(self):
        self.assertEqual(map_line([(10, 0, 11, 3)], 20), 23)

    def test_line_above_a_hunk_stays(self):
        self.assertEqual(map_line([(10, 0, 11, 3)], 10), 10)

    def test_rewritten_line_maps_proportionally(self):
        self.assertEqual(map_line([(10, 2, 10, 4)], 11), 12)

    def test_deleted_line_maps_to_the_deletion_point(self):
        self.assertEqual(map_line([(10, 5, 9, 0)], 12), 9)

    def test_whole_region_deleted_is_orphan(self):
        self.assertIsNone(map_region([(10, 5, 9, 0)], 11, 11, 12))

    def test_partly_deleted_region_stays(self):
        self.assertEqual(map_region([(10, 5, 9, 0)], 12, 12, 16), (9, 9, 11))

    def test_large_rewrite_is_capped_to_the_radius(self):
        self.assertEqual(map_region([(10, 100, 10, 100)], 50, 10, 109), (50, 45, 55))

    def test_insertion_inside_a_region_does_not_widen_it(self):
        self.assertEqual(map_region([(100, 0, 101, 1000)], 100, 95, 105), (100, 95, 105))

    def test_a_rewritten_line_takes_the_new_range_of_its_hunk_up_to_the_radius(self):
        self.assertEqual(map_region([(10, 1, 10, 11)], 10, 10, 10), (10, 10, 15))

    def test_a_rewrite_into_a_few_lines_takes_all_of_them(self):
        self.assertEqual(map_region([(10, 2, 10, 3)], 11, 11, 11), (11, 10, 12))

    def test_only_the_end_inside_a_rewrite_is_widened(self):
        self.assertEqual(map_region([(10, 2, 10, 4)], 8, 8, 10), (8, 8, 13))
        self.assertEqual(map_region([(10, 2, 10, 4)], 14, 11, 14), (16, 11, 16))

    def test_a_rewrite_keeps_the_shift_of_the_hunks_above(self):
        self.assertEqual(map_region([(1, 0, 2, 3), (10, 1, 13, 2)], 10, 10, 10), (13, 13, 14))

    def test_a_pure_deletion_inside_the_region_does_not_widen_it(self):
        self.assertEqual(map_region([(10, 2, 9, 0)], 12, 11, 12), (10, 9, 10))

    def test_no_hunks_is_identity(self):
        self.assertEqual(map_region([], 7, 5, 9), (7, 5, 9))


class ParseTest(unittest.TestCase):
    def test_parse_follows_a_rename(self):
        out = parse_u0(RENAME_DIFF)
        self.assertEqual(out["old.cc"]["path"], "new.cc")
        self.assertFalse(out["old.cc"]["deleted"])
        self.assertEqual(out["old.cc"]["hunks"], [(1, 0, 2, 2)])

    def test_parse_marks_a_deleted_file(self):
        out = parse_u0(DELETE_DIFF)
        self.assertTrue(out["gone.cc"]["deleted"])
        self.assertEqual(out["gone.cc"]["hunks"], [(1, 2, 0, 0)])


class DiffU0Test(unittest.TestCase):
    def test_returns_text_for_local_commits_and_none_otherwise(self):
        r = Repo(self)
        a = r.commit({"f.txt": "1\n2\n3\n"})
        b = r.commit({"f.txt": "1\n2\nx\n3\n"})
        out = gitutil.diff_u0(r.d, a, b)
        self.assertIn("@@ -2,0 +3 @@", out)
        self.assertIsNone(gitutil.diff_u0(r.d, "0123456789abcdef0123456789abcdef01234567", b))
        self.assertIsNone(gitutil.diff_u0(r.d, "nonsense", b))


if __name__ == "__main__":
    unittest.main()
