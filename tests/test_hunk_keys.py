"""Positional hunk keys: reviewed (sub-)hunks of a partially reviewed file are not resent."""
import unittest
from collections import Counter

from review.diff_split import build_chunks, parse_unified_diff, split_hunks
from review.fingerprint import decode_seen, encode_seen, hunk_key, pack, unpack, whole_hash
from review.hunks import diff_hunk_keys, diff_prints, select_file
from review.render import state_marker
from review.seen import update_hunks, update_seen
from review.state_fit import fit_state
from tests.test_hunks_split import HDR, new_file

LINES = ["row_%04d = compute(%04d)" % (i, i) for i in range(1, 401)]
CFG = {"chunk_max_tokens": 400, "max_chunks": 2, "exclude_globs": []}
LIMIT = int(CFG["chunk_max_tokens"] * 3.5)


def big():
    return parse_unified_diff(new_file(LINES))[0]


def scope_of(f, lines, sel, prev_hunks=()):
    return {"targets": [sel["file"]] if sel["file"] else [], "blobs": {"f.py": lines},
            "head_prints": {"f.py": sel["head"]}, "prev_seen": {"f.py": Counter()},
            "hunk_keys": {"f.py": sel["keys"]}, "prior": {"f.py": sel["prior"]},
            "prev_hunks": {"f.py": set(prev_hunks)}}


class HunkKeyTest(unittest.TestCase):
    def test_key_is_position_and_content(self):
        a, b = b"\x01\x02\x03\x04", b"\x05\x06\x07\x08"
        self.assertEqual(hunk_key(3, [a, b]), hunk_key(3, [b, a]))
        self.assertNotEqual(hunk_key(3, [a, b]), hunk_key(4, [a, b]))
        self.assertNotEqual(hunk_key(3, [a, b]), hunk_key(3, [a]))

    def test_key_survives_split_and_chunking(self):
        sel = select_file(big(), LINES, None, None, limit_chars=LIMIT)
        self.assertGreater(len(sel["keys"]), 3)
        chunks, _, cut = build_chunks([sel["file"]], dict(CFG, max_chunks=99))
        self.assertEqual(cut, [])
        got = set()
        for c in chunks:
            got |= diff_hunk_keys(c["diff"], {"f.py": LINES}).get("f.py", set())
        self.assertEqual(got, sel["keys"])

    def test_truncated_hunk_has_no_key(self):
        f = parse_unified_diff(new_file(LINES))[0]
        chunks, _, cut = build_chunks([f], CFG)  # unsplit hunk: truncated
        self.assertEqual(cut, ["f.py"])
        self.assertEqual(diff_hunk_keys(chunks[0]["diff"], {"f.py": LINES}), {})

    def test_capped_run_records_ok_keys_and_resumes(self):
        sel = select_file(big(), LINES, None, None, limit_chars=LIMIT)
        chunks, skipped, _ = build_chunks([sel["file"]], CFG)
        self.assertEqual((len(chunks), skipped), (2, ["f.py"]))
        scope = scope_of(big(), LINES, sel)
        results = [{"index": 0, "status": "ok"}, {"index": 1, "status": "failed"}]
        keys = update_hunks(scope, chunks, results)["f.py"]
        self.assertEqual(keys, diff_hunk_keys(chunks[0]["diff"], {"f.py": LINES})["f.py"])
        self.assertEqual(update_hunks(scope, chunks, [dict(r, status="failed") for r in results]),
                         {})  # a failed chunk records no keys
        nxt = select_file(big(), LINES, Counter(), None, limit_chars=LIMIT,
                          seen_hunks=Counter(keys))
        first = split_hunks(chunks[0]["diff"])[1]
        sent = split_hunks(nxt["file"]["text"])[1]
        self.assertFalse(set(first) & set(sent))
        self.assertEqual(len(sent), len(sel["keys"]) - len(keys))
        self.assertEqual(sum(nxt["prior"].values()), sum(diff_prints(
            chunks[0]["diff"], {"f.py": LINES})["f.py"].values()))

    def test_full_review_drops_keys(self):
        sel = select_file(big(), LINES, None, None, limit_chars=LIMIT)
        chunks, _, _ = build_chunks([sel["file"]], dict(CFG, max_chunks=99))
        scope = scope_of(big(), LINES, sel)
        res = [{"index": c["index"], "status": "ok"} for c in chunks]
        self.assertEqual(update_seen(scope, chunks, res), {"f.py": sel["head"]})
        self.assertEqual(update_hunks(scope, chunks, res), {})

    def test_seen_hunks_ignored_on_first_review(self):
        sel = select_file(big(), LINES, None, None, limit_chars=LIMIT)
        again = select_file(big(), LINES, None, None, limit_chars=LIMIT,
                            seen_hunks=Counter(sel["keys"]))
        self.assertEqual(again["file"]["text"], sel["file"]["text"])

    def test_pasted_duplicate_at_new_position_reviewed(self):
        block = ["dup_%d = %d" % (i, i) for i in range(5)]
        lines = ["ctx_%02d" % i for i in range(30)]
        lines[2:2] = block
        one = ("diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n@@ -2,2 +2,7 @@\n"
               " %s\n" % lines[1] + "".join("+%s\n" % x for x in block) + " %s\n" % lines[7])
        sel = select_file(parse_unified_diff(one)[0], lines, Counter(), None)
        reviewed = Counter(sel["keys"])
        lines2 = lines[:20] + block + lines[20:]
        two = one + "@@ -15,2 +20,7 @@\n %s\n" % lines2[19] + "".join(
            "+%s\n" % x for x in block) + " %s\n" % lines2[25]
        f2 = parse_unified_diff(two)[0]
        got = select_file(f2, lines2, Counter(), None, seen_hunks=reviewed)
        self.assertEqual([h.split("\n", 1)[0] for h in split_hunks(got["file"]["text"])[1]],
                         ["@@ -15,2 +20,7 @@"])
        self.assertEqual(got["added"], set(range(21, 26)))


class FullFirstTest(unittest.TestCase):
    def test_encode_seen_compacts_full_files_first(self):
        part = Counter(hunk_key(i, [b"p"]) for i in range(50))
        done = Counter(hunk_key(i, [b"d"]) for i in range(10))
        raw, files = encode_seen({"part.py": part, "done.py": done}, cap=len(pack(part)),
                                 full={"done.py"})
        self.assertEqual((set(raw), set(files)), ({"part.py"}, {"done.py"}))
        self.assertEqual(decode_seen({"seen": raw, "seen_files": files})[1],
                         {"done.py": whole_hash(done)})

    def test_fit_state_pops_partial_last_and_drop_clears_hunks(self):
        part = pack(Counter(hunk_key(i, [b"p"]) for i in range(50)))
        done = pack(Counter(hunk_key(i, [b"d"]) for i in range(10)))
        st = {"v": 2, "sha": "h", "complete": False, "seen": {"part.py": part, "done.py": done},
              "seen_files": {}, "seen_hunks": {"part.py": "AQIDBA=="}, "findings": []}
        out = fit_state(st, budget=len(state_marker(st)) - 10, partial={"part.py"})
        self.assertEqual((set(out["seen"]), set(out["seen_files"])), ({"part.py"}, {"done.py"}))
        out = fit_state(st, budget=10, partial={"part.py"})
        self.assertEqual((out["seen"], out["seen_files"], out["seen_hunks"]), ({}, {}, {}))
        self.assertEqual(unpack(st["seen_hunks"]["part.py"]), Counter([b"\x01\x02\x03\x04"]))


if __name__ == "__main__":
    unittest.main()
