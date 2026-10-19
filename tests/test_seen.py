import unittest
from collections import Counter

from review.diff_split import MARKER, build_chunks, parse_unified_diff
from review.fingerprint import decode_seen, encode_seen, fp_line, whole_hash
from review.hunks import diff_prints, select_file
from review.seen import update_seen


def lines_of(tag, n=10):
    return ["%s_%d = %d" % (tag, i, i) for i in range(1, n + 1)]


def new_file(path, lines):
    return ("diff --git a/{0} b/{0}\nnew file mode 100644\n--- /dev/null\n+++ b/{0}\n"
            "@@ -0,0 +1,{1} @@\n".format(path, len(lines))
            + "".join("+%s\n" % x for x in lines))


def scope_for(files, prev=None):
    """Synthetic Scope: every file is new in the PR and fully targeted."""
    blobs = {p: lines for p, lines in files.items()}
    diffs = {p: new_file(p, lines) for p, lines in files.items()}
    head = {p: diff_prints(d, blobs)[p] for p, d in diffs.items()}
    return {"targets": [parse_unified_diff(d)[0] for d in diffs.values()], "blobs": blobs,
            "head_prints": head, "prev_seen": {p: (prev or {}).get(p, Counter()) for p in files}}, diffs


def chunk(i, *diffs):
    return {"index": i, "files": [], "diff": "".join(diffs)}


def res(i, status="ok"):
    return {"index": i, "status": status}


class UpdateSeenTest(unittest.TestCase):
    def test_ok_chunk_marks_seen(self):
        scope, d = scope_for({"a.py": lines_of("a"), "b.py": lines_of("b")})
        got = update_seen(scope, [chunk(0, d["a.py"], d["b.py"])], [res(0)])
        self.assertEqual(got, scope["head_prints"])

    def test_failed_chunk_lines_stay_new(self):
        b = lines_of("b")
        old = Counter([fp_line(b, 1)])
        scope, d = scope_for({"a.py": lines_of("a"), "b.py": b}, prev={"b.py": old})
        for status in ("failed", "partial", "incomplete"):
            got = update_seen(scope, [chunk(0, d["a.py"]), chunk(1, d["b.py"])],
                              [res(0), res(1, status)])
            self.assertEqual(got, {"a.py": scope["head_prints"]["a.py"], "b.py": old})

    def test_skipped_target_stays_new(self):
        scope, d = scope_for({"a.py": lines_of("a"), "b.py": lines_of("b")})
        got = update_seen(scope, [chunk(0, d["a.py"])], [res(0)])
        self.assertEqual(set(got), {"a.py"})

    def test_truncated_tail_stays_new(self):
        a = lines_of("a")
        scope, d = scope_for({"a.py": a})
        cut = d["a.py"][:d["a.py"].index("+a_6")].rstrip("\n") + MARKER
        got = update_seen(scope, [chunk(0, cut)], [res(0)])
        self.assertEqual(got, {"a.py": Counter(fp_line(a, n) for n in range(1, 6))})

    def test_prev_counts_kept_for_untouched_files(self):
        scope, d = scope_for({"a.py": lines_of("a")})
        kept = Counter({b"\x01\x02\x03\x04": 2})
        scope["head_prints"]["c.py"] = Counter({b"\x01\x02\x03\x04": 1})
        scope["prev_seen"]["c.py"] = kept  # min(prev, head): never above head
        scope["head_prints"]["e.py"] = Counter()
        got = update_seen(scope, [chunk(0, d["a.py"])], [res(0)])
        self.assertEqual(got["c.py"], Counter({b"\x01\x02\x03\x04": 1}))
        self.assertNotIn("e.py", got)

    def test_whole_hash_file_counts_as_head(self):
        a = lines_of("a")
        scope, d = scope_for({"a.py": a})
        seen = update_seen(scope, [chunk(0, d["a.py"])], [res(0)])
        raw, files = encode_seen(seen, cap=0)
        self.assertEqual((raw, set(files)), ({}, {"a.py"}))
        _, whole = decode_seen({"seen": raw, "seen_files": files})
        sel = select_file(scope["targets"][0], a, Counter(), whole["a.py"])
        self.assertIsNone(sel["file"])
        self.assertEqual(whole["a.py"], whole_hash(sel["head"]))
        nxt = {"targets": [], "blobs": scope["blobs"], "head_prints": {"a.py": sel["head"]},
               "prev_seen": {"a.py": sel["head"]}}
        self.assertEqual(update_seen(nxt, [], []), {"a.py": sel["head"]})


    def test_two_units_of_one_file_in_one_chunk_all_counted(self):
        blob = ["ctx_%d" % i for i in range(1, 61)]
        blob[1], blob[54] = "new_a", "new_b"

        def body(at):  # one changed line (1-based `at`) with one context line each side
            return ("@@ -%d,3 +%d,3 @@\n %s\n-old\n+%s\n %s\n"
                    % (at - 1, at - 1, blob[at - 2], blob[at - 1], blob[at]))
        text = ("diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n" + body(2) + body(55))
        f = parse_unified_diff(text)[0]
        cfg = {"chunk_max_tokens": 30, "max_chunks": 5, "exclude_globs": []}
        chunks, skipped = build_chunks([f], cfg)
        self.assertEqual((len(chunks), skipped), (2, []))
        joined = "".join(c["diff"] for c in chunks)  # two units, header repeated
        self.assertEqual(joined.count("diff --git"), 2)
        want = diff_prints(text, {"f.py": blob})["f.py"]
        self.assertEqual(sum(want.values()), 4)  # 2 added + 2 removed
        self.assertEqual(diff_prints(joined, {"f.py": blob})["f.py"], want)
        scope = {"targets": [f], "blobs": {"f.py": blob}, "head_prints": {"f.py": want},
                 "prev_seen": {"f.py": Counter()}}
        got = update_seen(scope, [chunk(0, joined)], [res(0)])
        self.assertEqual(got, {"f.py": want})


if __name__ == "__main__":
    unittest.main()
