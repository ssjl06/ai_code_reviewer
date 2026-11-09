import json
import hashlib
import unittest
from collections import Counter

from review.fingerprint import decode_seen, encode_seen, pack, whole_hash
from review.render import render_body, state_marker
from review.ledger import advance
from review.scope import next_state, settle_state
from review.state_fit import MARKER_BUDGET, fit_state
from review.validate import normalize_state
from tests.test_render import META, REVIEW


def prints(path, n=5):
    return Counter(hashlib.sha1(("%s:%d" % (path, i)).encode()).digest()[:4] for i in range(n))


def finding(i, blocking):
    return {"id": "%08x" % i, "file": "src/module_%03d/component_file.py" % (i % 50),
            "line": i, "anchor": "0a0b0c0d", "title": "제목 " * 20 + str(i),
            "severity": "major" if blocking else "minor", "blocking": blocking,
            "confidence": 90, "detail": "d"}


def big_state():
    paths = ["src/package_%04d/some_module_name_x.py" % i for i in range(1000)]
    heads = {p: prints(p) for p in paths}
    scope = {"targets": [], "blobs": {}, "head_prints": heads, "prev_seen": heads}
    found = [finding(i, i % 40 == 0) for i in range(205)]
    review = {"findings": found, "previous": [], "complete": True}
    return next_state("h", review, scope, [], []), found


def issue(i, status="open", long_titles=False, blocking=False):
    return {"id": "%08x" % i, "file": "src/module_%03d/component_file.py" % (i % 50),
            "line": 10 + i % 90, "lo": 9 + i % 90, "hi": 11 + i % 90, "status": status,
            "severity": "major" if blocking else "minor", "blocking": blocking,
            "anchor": "0a0b0c0d", "title": ("제목 " * 20 if long_titles else "t") + str(i),
            "excerpt": "d" * 100 if long_titles else "", "orphan": False}


def state_with(closed=0, open_=0, long_titles=False, seen=None, blocking_every=0):
    """A v3 state with `open_` open issues (newest first) followed by `closed` closed ones."""
    issues = [issue(i, long_titles=long_titles,
                    blocking=bool(blocking_every) and i % blocking_every == 0)
              for i in range(open_)]
    issues += [issue(open_ + i, status="waived" if i % 2 else "resolved",
                     long_titles=long_titles) for i in range(closed)]
    return {"v": 3, "sha": "h", "complete": True, "seen": dict(seen or {}), "seen_files": {},
            "seen_hunks": {}, "issues": issues, "closed": {}, "recheck_pending": ["x1"]}


def _seen_over_budget():
    paths = ["src/package_%04d/some_module_name_x.py" % i for i in range(1000)]
    return encode_seen({p: prints(p) for p in paths})[0]


SEEN_OVER_BUDGET = _seen_over_budget()


def count(st, status):
    return sum(1 for i in normalize_state(st)["issues"] if i["status"] == status)


class FitStateTest(unittest.TestCase):
    def test_small_state_untouched(self):
        st = state_with(closed=1, open_=1)
        st["seen"] = {"a": "AAAAAA=="}
        self.assertEqual(fit_state(st), st)

    def test_moves_largest_files_first(self):
        seen = {"big": prints("big", 400), "small": prints("small", 2)}
        raw, files = encode_seen(seen)
        st = {"v": 2, "sha": "h", "complete": True, "seen": raw, "seen_files": files,
              "findings": []}
        out = fit_state(st, budget=len(state_marker(st)) - 100)
        self.assertEqual(set(out["seen"]), {"small"})
        self.assertEqual(decode_seen(out)[1], {"big": whole_hash(seen["big"])})

    def test_blanks_display_before_dropping_seen(self):
        st = state_with(open_=3, long_titles=True)
        st.update(seen={"a": "AAAAAA=="}, seen_files={"b": "AAAAAA=="})
        out = fit_state(st, budget=len(state_marker(st)) - 100)
        self.assertEqual(out["seen_files"].get("b"), "AAAAAA==")
        self.assertIn("a", {**out["seen"], **out["seen_files"]})
        self.assertIn(None, [x.get("title") for x in out["issues"]])

    def test_blanks_display_of_blocking_issues_before_dropping_seen(self):
        st = state_with(open_=4, long_titles=True, blocking_every=1)
        st.update(seen={"a": "AAAAAA=="})
        blank = dict(st, issues=[{k: v for k, v in i.items() if k not in ("title", "excerpt")}
                                 for i in st["issues"]])
        out = fit_state(st, budget=len(state_marker(blank)) + 60)
        self.assertNotEqual({**out["seen"], **out["seen_files"]}, {})
        self.assertEqual(out["issues"], blank["issues"])

    def test_drops_seen_when_blank_display_is_not_enough_and_restores_what_fits(self):
        st = state_with(open_=3, long_titles=True, seen=SEEN_OVER_BUDGET)
        st["seen_hunks"] = {"a": "AAAAAA=="}
        out = fit_state(st, budget=len(state_marker(dict(st, seen={}, seen_hunks={}))))
        self.assertEqual((out["seen"], out["seen_files"], out["seen_hunks"]), ({}, {}, {}))
        self.assertEqual([x["title"] for x in out["issues"]], [x["title"] for x in st["issues"]])

    def test_cut_prints_survive_a_tight_budget(self):
        cut = {"a.min.js": "AQIDBA=="}
        st = dict(state_with(closed=3000, open_=5, long_titles=True, seen=SEEN_OVER_BUDGET),
                  cut=cut)
        out = fit_state(st, budget=20000)
        self.assertEqual(out["seen"], {})  # the budget forced the last steps
        self.assertEqual(out["cut"], cut)
        self.assertEqual(normalize_state(json.loads(json.dumps(out)))["cut"], cut)

    def test_fit_keeps_200_closed_issues_and_the_seen_data(self):
        seen = {"src/p%03d.py" % i: pack(prints("p%d" % i, 40)) for i in range(200)}
        st = state_with(closed=200, seen=seen)
        self.assertGreater(len(state_marker(st)), MARKER_BUDGET)
        out = fit_state(st)
        self.assertEqual(len(normalize_state(out)["issues"]), 200)
        self.assertLessEqual(len(state_marker(out)), MARKER_BUDGET)
        self.assertGreater(len(out["seen_files"]), 0)
        self.assertEqual(len(out["seen"]) + len(out["seen_files"]), 200)

    def test_fit_keeps_200_closed_issues_with_seen_too_large_for_hashes(self):
        st = state_with(closed=200, seen=SEEN_OVER_BUDGET)
        self.assertGreater(len(state_marker(st)), MARKER_BUDGET)
        out = fit_state(st)
        self.assertEqual(len(normalize_state(out)["issues"]), 200)
        self.assertLessEqual(len(state_marker(out)), MARKER_BUDGET)
        self.assertEqual((out["seen"], out["seen_files"]), ({}, {}))

    def test_160_open_blocking_issues_with_long_text_fit(self):
        issues = [dict(issue(i, blocking=True), title="한" * 60, excerpt="설" * 200)
                  for i in range(160)]
        st = dict(state_with(), issues=issues)
        self.assertGreater(len(state_marker(st)), MARKER_BUDGET)
        out = fit_state(st)
        self.assertLessEqual(len(state_marker(out)), MARKER_BUDGET)
        self.assertEqual(count(out, "open"), 160)
        kept = [bool(i.get("title")) for i in out["issues"]]
        self.assertIn(False, kept)
        self.assertEqual(kept, sorted(kept, reverse=True))  # only the oldest are blank

    def test_blocking_display_goes_only_after_all_non_blocking_display(self):
        st = state_with(open_=150, long_titles=True, blocking_every=2)
        st["issues"] = [dict(i, excerpt="설" * 200) for i in st["issues"]]
        out = fit_state(st)
        self.assertLessEqual(len(state_marker(out)), MARKER_BUDGET)
        self.assertFalse(any(i.get("title") for i in out["issues"] if not i["blocking"]))
        self.assertIn(None, [i.get("title") for i in out["issues"] if i["blocking"]])

    def test_closed_records_go_only_after_seen_and_all_display(self):
        seen = {"a": "AAAAAA=="}
        out = fit_state(state_with(closed=3000, open_=5, long_titles=True, seen=seen))
        self.assertGreater(out["dropped_closed"], 0)
        self.assertEqual((out["seen"], out["seen_files"]), ({}, {}))
        self.assertFalse(any(i.get("title") for i in out["issues"]))

    def test_fit_drops_display_of_closed_before_open(self):
        st = state_with(closed=800, open_=50, long_titles=True)
        self.assertGreater(len(state_marker(st)), MARKER_BUDGET)
        out = fit_state(st)
        self.assertLessEqual(len(state_marker(out)), MARKER_BUDGET)
        self.assertTrue(all(i["title"] for i in out["issues"] if i["status"] == "open"))
        self.assertNotIn("dropped_closed", out)
        closed = [i for i in normalize_state(out)["issues"] if i["status"] != "open"]
        self.assertEqual(len(closed), 800)
        self.assertTrue(all(not i["title"] and not i["anchor"] for i in closed))

    def test_fit_blanks_display_of_open_non_blocking_oldest_first(self):
        st = state_with(open_=150, long_titles=True, blocking_every=5)
        self.assertGreater(len(state_marker(st)), MARKER_BUDGET)
        out = fit_state(st)
        self.assertLessEqual(len(state_marker(out)), MARKER_BUDGET)
        self.assertEqual(len(out["issues"]), 150)
        self.assertTrue(all(i["title"] for i in out["issues"] if i["blocking"]))
        blank = [bool(i.get("title")) for i in out["issues"] if not i["blocking"]]
        self.assertIn(False, blank)
        self.assertEqual(blank, sorted(blank, reverse=True))  # only the oldest are blank

    def test_fit_never_drops_an_open_issue(self):
        st = fit_state(state_with(open_=200, long_titles=True))
        self.assertEqual(count(st, "open"), 200)
        self.assertEqual(st["recheck_pending"], ["x1"])

    def test_fit_drops_the_oldest_closed_only_as_a_last_resort(self):
        src = state_with(closed=3000, open_=5)
        st = fit_state(src)
        self.assertGreater(st["dropped_closed"], 0)
        self.assertLessEqual(len(state_marker(st)), MARKER_BUDGET)
        ids = [i["id"] for i in normalize_state(st)["issues"]]
        self.assertEqual(len(ids), 3005 - st["dropped_closed"])
        self.assertEqual(ids[:5], [i["id"] for i in src["issues"][:5]])
        self.assertEqual(set(ids), {i["id"] for i in src["issues"][:len(ids)]})  # oldest went first

    def test_dropped_closed_accumulates(self):
        st = fit_state(dict(state_with(closed=3000), dropped_closed=7))
        n = len(normalize_state(st)["issues"])
        self.assertEqual(st["dropped_closed"], 7 + 3000 - n)

    def test_body_says_when_closed_records_were_dropped(self):
        body = render_body(REVIEW(), [], META, dict(state_with(), dropped_closed=3))
        self.assertIn("오래된 종료 이슈 기록 3건 삭제", body)
        self.assertNotIn("오래된 종료 이슈", render_body(REVIEW(), [], META, state_with()))

    def test_huge_pr_body_fits_and_keeps_blocking(self):
        st, found = big_state()
        self.assertLessEqual(len(state_marker(st)), MARKER_BUDGET)
        self.assertEqual((st["seen"], st["seen_files"]), ({}, {}))
        kept = {f["id"]: f for f in st["issues"]}
        self.assertEqual(set(kept), {f["id"] for f in found})  # open issues are never dropped
        self.assertTrue(all(kept[f["id"]]["title"] for f in found if f["blocking"]))
        self.assertTrue(kept[found[0]["id"]]["title"])  # newest keeps its display text
        body = render_body(REVIEW(), [], META, st)
        self.assertLess(len(body), 65536)


class ViaSurvivesTest(unittest.TestCase):
    """`via` is identity data: it survives every path that rebuilds an issue."""

    def test_via_survives_next_state_fit_normalize_and_advance(self):
        paths = ["src/package_%04d/some_module_name_x.py" % i for i in range(1000)]
        heads = {p: prints(p) for p in paths}
        scope = {"targets": [], "blobs": {}, "head_prints": heads, "prev_seen": heads,
                 "closed": [dict(issue(900, status="resolved"), via="000003e8")]}
        found = [dict(finding(i, i % 40 == 0), via="%08x" % (5000 + i)) for i in range(205)]
        prev = [dict(issue(800), via="00000bb8"),
                dict(issue(801), status="resolved", via="00000bb9")]
        review = {"findings": found, "previous": prev, "complete": True}
        st = next_state("h", review, scope, [], [])
        self.assertLessEqual(len(state_marker(st)), MARKER_BUDGET)
        self.assertTrue(st["closed"])  # compacted
        self.assertTrue(any(not i.get("title") for i in st["issues"]))  # blanked
        want = {f["id"]: f["via"] for f in found}
        want.update({"00000320": "00000bb8", "00000321": "00000bb9", "00000384": "000003e8"})
        back = normalize_state(st)
        self.assertEqual({i["id"]: i.get("via") for i in back["issues"]}, want)
        # settle_state rebuilds the issues from the same review
        settled = normalize_state(settle_state("h2", review, dict(scope, prev=back)))
        self.assertEqual({i["id"]: i.get("via") for i in settled["issues"]}, want)
        # advance: mapped by hunks, orphaned by a deleted file, relocated, by anchor
        f0, f1 = "src/module_000/component_file.py", "src/module_001/component_file.py"
        u0 = {f0: {"path": f0, "deleted": False, "hunks": [(1, 0, 1, 3)]},
              f1: {"path": f1, "deleted": True, "hunks": []}}
        for moved in (advance(back["issues"], u0, {})[0], advance(back["issues"], None, {})[0]):
            self.assertEqual({i["id"]: i.get("via") for i in moved}, want)
        lines = ["l%d" % n for n in range(200)]
        lone = dict(issue(0), via="v", line=50, lo=50, hi=50)
        u0 = {f0: {"path": f0, "deleted": False, "hunks": [(50, 1, 50, 0)]}}
        out, _ = advance([lone], u0, {f0: lines[:49] + lines[50:] + ["l49"]}, {f0: lines})
        self.assertEqual((out[0]["line"], out[0]["orphan"], out[0]["via"]), (200, False, "v"))
        out, _ = advance([lone], u0, {f0: lines[:49] + lines[50:]}, {f0: lines})
        self.assertEqual((out[0]["orphan"], out[0]["via"]), (True, "v"))


if __name__ == "__main__":
    unittest.main()
