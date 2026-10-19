import os
import unittest

from review.config import load_config
from review.diff_split import (build_chunks, estimate_tokens, line_map,
                               parse_unified_diff, priority)

CFG = load_config()
HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "fixtures", "sample.diff"), encoding="utf-8") as _f:
    SAMPLE = _f.read()
QUOTED = ('diff --git "a/my dir/\\355\\225\\234\\352\\270\\200.py" '
          '"b/my dir/\\355\\225\\234\\352\\270\\200.py"\n'
          'index 1..2 100644\n--- "a/my dir/\\355\\225\\234\\352\\270\\200.py"\n'
          '+++ "b/my dir/\\355\\225\\234\\352\\270\\200.py"\n@@ -1 +1 @@\n-a\n+b\n')
UNQUOTED = ("diff --git a/my dir/x.py b/my dir/x.py\nindex 1..2 100644\n"
            "--- a/my dir/x.py\t\n+++ b/my dir/x.py\t\n@@ -1 +1 @@\n-a\n+b\n")


def make_file(path="src/big.cu", hunks=1, tokens_each_hunk=100):
    header = "diff --git a/%s b/%s\n--- a/%s\n+++ b/%s\n" % ((path,) * 4)
    body = ""
    for i in range(hunks):
        start = 1 + i * 1000
        lines = ["+" + "x" * 60 for _ in range(int(tokens_each_hunk * 3.5 / 61))]
        body += "@@ -%d,0 +%d,%d @@\n%s\n" % (start, start, len(lines), "\n".join(lines))
    return {"path": path, "old_path": None, "status": "M", "binary": False,
            "text": header + body, "added_lines": [], "right_lines": []}


def make_files(n, tokens_each):
    return [make_file("src/f%d.cu" % i, 1, tokens_each) for i in range(n)]


def make_named(paths):
    return [make_file(p, 1, 50) for p in paths]


class DiffSplitTest(unittest.TestCase):
    def test_parse_statuses_and_lines(self):
        by = {f["path"]: f for f in parse_unified_diff(SAMPLE)}
        self.assertEqual(by["src/k.cu"]["status"], "M")
        self.assertEqual(by["docs/a.md"]["status"], "A")
        self.assertEqual(by["old.py"]["status"], "D")
        self.assertTrue(by["img.png"]["binary"])
        self.assertEqual((by["y.h"]["status"], by["y.h"]["old_path"]), ("R", "x.h"))
        self.assertEqual(by["src/k.cu"]["added_lines"], [11, 12, 40])
        self.assertIn(10, by["src/k.cu"]["right_lines"])
        self.assertNotIn(11, line_map(list(by.values()), "right_lines")["y.h"])
        self.assertEqual(line_map(list(by.values()), "added_lines")["src/k.cu"], {11, 12, 40})

    def test_quoted_and_unicode_paths(self):
        self.assertEqual(parse_unified_diff(QUOTED)[0]["path"], "my dir/한글.py")
        self.assertEqual(parse_unified_diff(UNQUOTED)[0]["path"], "my dir/x.py")

    def test_priority_order(self):
        self.assertEqual([priority(p) for p in
            ["a.cu", "ci.yml", "tests/test_a.py", "README.md", "LICENSE"]], [0, 1, 2, 3, 4])
        self.assertEqual([priority(p) for p in ["CMakeLists.txt", "src/requirements.txt",
                                                "docs/CMakeLists.txt", "notes.txt"]], [1, 1, 1, 3])

    def test_estimate_tokens(self):
        self.assertEqual(estimate_tokens("x" * 7), 2)
        self.assertEqual(estimate_tokens("x" * 8), 3)

    def test_excluded_deleted_binary_not_chunked(self):
        chunks, skipped, cut = build_chunks(parse_unified_diff(SAMPLE), CFG)
        self.assertEqual(cut, [])
        names = [p for c in chunks for p in c["files"]]
        for bad in ("vendor/z.c", "old.py", "img.png"):
            self.assertNotIn(bad, names)
        self.assertIn("src/k.cu", chunks[0]["files"])
        self.assertEqual(skipped, [])

    def test_chunk_limit_and_cap(self):
        cfg = dict(CFG, chunk_max_tokens=1000, max_chunks=2)
        chunks, skipped, _ = build_chunks(make_files(n=5, tokens_each=800), cfg)
        self.assertEqual(len(chunks), 2)
        self.assertTrue(all(c["est_tokens"] <= 1000 for c in chunks))
        self.assertEqual(len(skipped), 3)

    def test_large_file_split_by_hunks(self):
        chunks, _, _ = build_chunks([make_file(hunks=4, tokens_each_hunk=600)],
                                 dict(CFG, chunk_max_tokens=1300))
        self.assertEqual(len(chunks), 2)
        for c in chunks:
            self.assertEqual(c["diff"].count("diff --git"), 2)
            self.assertEqual(c["diff"].count("+++ b/"), 2)

    def test_oversized_single_hunk_truncated(self):
        chunks, _, cut = build_chunks([make_file(hunks=1, tokens_each_hunk=5000)],
                                      dict(CFG, chunk_max_tokens=1000))
        self.assertEqual((len(chunks), cut), (1, [chunks[0]["files"][0]]))
        self.assertLessEqual(chunks[0]["est_tokens"], 1000)
        self.assertIn("[truncated by ai-review]", chunks[0]["diff"])

    def test_related_files_grouped(self):
        chunks, _, _ = build_chunks(make_named(["a/foo.cu", "b/bar.py", "a/foo.cuh"]), CFG)
        self.assertEqual(chunks[0]["files"][:2], ["a/foo.cu", "a/foo.cuh"])


if __name__ == "__main__":
    unittest.main()
