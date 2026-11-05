import json
import os
import tempfile
import unittest
from collections import Counter

from review.aggregate import merge
from review.fingerprint import decode_seen, fp_line
from review.cli_util import parse_overrides, read_state_file
from review.diff_split import build_chunks
from review.ledger import assign_recheck, pending_after, shown_lines
from review.scope import determine, next_state
from review.validate import normalize_state
from tests.test_scope import CFG, code, run, state_for

NEW = {"id": "n1", "file": "a", "line": 1, "anchor": "0a0b0c0d", "title": "t",
       "severity": "major", "blocking": True, "confidence": 90, "detail": "d"}
P1 = b"\x01\x02\x03\x04"


def empty_scope(**kw):
    s = {"targets": [], "blobs": {}, "head_prints": {}, "prev_seen": {}, "closed": []}
    s.update(kw)
    return s


def issue(id_, status="open", **kw):
    e = {"id": id_, "file": "a", "line": 5, "lo": 4, "hi": 6, "status": status,
         "severity": "major", "blocking": True, "anchor": "0a0b0c0d", "title": "t",
         "excerpt": "", "orphan": False}
    e.update(kw)
    return e


class NextStateTest(unittest.TestCase):
    def test_next_state_v3_shape(self):
        prev = [dict(issue("p1"), pre="ask", located=True),
                issue("p2", "resolved"),
                dict(issue("p3", "unclear", anchor=""), note="x")]
        review = {"findings": [NEW], "previous": prev, "complete": True}
        scope = empty_scope(head_prints={"a": Counter([P1])}, prev_seen={"a": Counter([P1])},
                            closed=[issue("c1", "waived")])
        st = next_state("h", review, scope, [], [])
        self.assertEqual(set(st), {"v", "sha", "complete", "seen", "seen_files", "seen_hunks",
                                   "issues", "closed", "recheck_pending"})
        self.assertEqual((st["v"], st["sha"], st["complete"], st["closed"], st["recheck_pending"]),
                         (3, "h", True, {}, []))
        self.assertEqual([(i["id"], i["status"]) for i in st["issues"]],
                         [("n1", "open"), ("p1", "open"), ("p3", "open"),
                          ("p2", "resolved"), ("c1", "waived")])
        self.assertEqual(set(st["issues"][1]), {"id", "file", "line", "lo", "hi", "status",
                                                "severity", "blocking", "anchor", "title"})
        self.assertEqual(st["issues"][2]["anchor"], "")
        self.assertEqual(decode_seen(st)[0], {"a": Counter([P1])})
        json.dumps(st)

    def test_new_finding_becomes_an_open_issue(self):
        review = {"findings": [NEW], "previous": [], "complete": True}
        st = next_state("h", review, empty_scope(), [], [])
        i = st["issues"][0]
        self.assertEqual((i["id"], i["status"], i["line"], i["lo"], i["hi"], i["blocking"]),
                         ("n1", "open", 1, 1, 1, True))
        self.assertEqual((i["title"], i["excerpt"], i["anchor"]), ("t", "d", "0a0b0c0d"))
        self.assertNotIn("orphan", i)

    def test_closed_issue_stays_in_the_next_state(self):
        review = {"findings": [], "previous": [], "complete": True}
        st = next_state("h", review, empty_scope(closed=[issue("w1", "waived")]), [], [])
        self.assertEqual([(i["id"], i["status"]) for i in normalize_state(st)["issues"]],
                         [("w1", "waived")])

    def test_unclear_is_stored_as_open_and_a_file_left_behind_as_orphan(self):
        prev = [issue("u1", "unclear"), dict(issue("g1", "resolved"), pre="resolved"),
                dict(issue("o1", "resolved", orphan=True), pre="orphan")]
        st = next_state("h", {"findings": [], "previous": prev, "complete": True},
                        empty_scope(), [], [])
        self.assertEqual([(i["id"], i["status"], i.get("orphan", False)) for i in st["issues"]],
                         [("u1", "open", False), ("g1", "resolved", True), ("o1", "resolved", True)])

    def test_a_link_gives_a_closed_orphan_its_place_and_it_stays_closed(self):
        closed = [issue("c1", "waived", orphan=True), issue("c2", "resolved", orphan=True),
                  issue("c3", "resolved", orphan=True)]
        review = {"findings": [], "previous": [], "complete": True,
                  "links": [{"id": "c1", "file": "b.cc", "line": 50}],
                  "duplicates": [{"file": "d.cc", "line": 9, "dup_of": "c2"}]}
        st = next_state("h", review, empty_scope(closed=closed), [], [])
        got = {i["id"]: (i["status"], i["file"], i["line"], i["lo"], i["hi"], i.get("orphan", False))
               for i in st["issues"]}
        self.assertEqual(got, {"c1": ("waived", "b.cc", 50, 50, 50, False),
                               "c2": ("resolved", "d.cc", 9, 9, 9, False),
                               "c3": ("resolved", "a", 5, 4, 6, True)})

    def test_a_link_to_a_closed_issue_with_a_region_changes_nothing(self):
        review = {"findings": [], "previous": [], "complete": True,
                  "links": [{"id": "c1", "file": "b.cc", "line": 50}]}
        st = next_state("h", review, empty_scope(closed=[issue("c1", "waived")]), [], [])
        self.assertEqual((st["issues"][0]["file"], st["issues"][0]["line"]), ("a", 5))

    def test_recheck_pending_and_dropped_closed_are_carried(self):
        review = {"findings": [], "previous": [], "complete": True, "recheck_pending": ["p1"]}
        st = next_state("h", review, empty_scope(dropped_closed=4), [], [])
        self.assertEqual((st["recheck_pending"], st["dropped_closed"]), (["p1"], 4))
        st = next_state("h", {"findings": [], "previous": [], "complete": True},
                        empty_scope(), [], [])
        self.assertEqual((st["recheck_pending"], "dropped_closed" in st), ([], False))

    def test_incomplete_keeps_head_sha(self):
        review = {"findings": [], "previous": [], "complete": False, "skipped": ["x"]}
        st = next_state("h", review, empty_scope(), [], [])
        self.assertEqual((st["sha"], st["complete"], st["seen"], st["seen_files"]),
                         ("h", False, {}, {}))


class HelpersTest(unittest.TestCase):
    def test_parse_overrides(self):
        got = parse_overrides(["max_chunks=2", "claude_bin=/a/b", "exclude_globs=[\"x\"]"])
        self.assertEqual(got, {"max_chunks": 2, "claude_bin": "/a/b", "exclude_globs": ["x"]})
        with self.assertRaises(ValueError):
            parse_overrides(["novalue"])

    def test_read_state_file(self):
        self.assertIsNone(read_state_file(None))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            with open(p, "w") as f:
                json.dump({"sha": "x", "findings": []}, f)
            self.assertIsNone(read_state_file(p))  # v1 state = no previous state
            with open(p, "w") as f:
                json.dump({"v": 2, "sha": "x", "complete": True, "findings": []}, f)
            self.assertEqual(read_state_file(p), {"v": 3, "sha": "x", "complete": True,
                                                  "seen": {}, "seen_files": {}, "seen_hunks": {},
                                                  "issues": [], "recheck_pending": []})


def repo_with_review(findings, files=None, base_files=None):
    """A temp repo (base on main, a.cc and b.cc on feat) reviewed once with `findings`."""
    tmp = tempfile.TemporaryDirectory()
    d = tmp.name
    for args in (("init", "-q", "-b", "main"), ("config", "user.email", "t@example.com"),
                 ("config", "user.name", "t")):
        run(d, *args)
    commit(d, dict({"base.py": code("base")}, **(base_files or {})))
    base = run(d, "rev-parse", "HEAD")
    run(d, "checkout", "-q", "-b", "feat")
    head = commit(d, files or {"a.cc": code("a", 60), "b.cc": code("b")})
    state = state_for(determine(d, base, head, None, CFG), head, findings)
    return tmp, d, base, head, state


def commit(d, files):
    for name, text in files.items():
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(text)
        run(d, "add", name)
    run(d, "commit", "-q", "--allow-empty", "-m", "m")
    return run(d, "rev-parse", "HEAD")


def open_lines(path):
    return code("a", 60).splitlines() if path == "a.cc" else code("b").splitlines()


def issue_finding(path="a.cc", line=43, id_="i1"):
    return {"id": id_, "file": path, "line": line, "anchor": fp_line(open_lines(path), line).hex(),
            "title": "t", "severity": "major", "blocking": True, "confidence": 90, "detail": "d"}


def determine_after_push(add_lines_at=None, issue_line=43, change_file=None, issue_file="a.cc"):
    """The scope of a push after a review that flagged `issue_file:issue_line`: lines added to
    a.cc at `add_lines_at` and/or a line of `change_file` changed."""
    tmp, d, base, head, state = repo_with_review([issue_finding(issue_file, issue_line)])
    with tmp:
        files = {}
        if add_lines_at:
            lines = code("a", 60).splitlines(True)
            lines.insert(add_lines_at - 1, "null_check = 1\n")
            files["a.cc"] = "".join(lines)
        if change_file:
            files[change_file] = "".join(open_lines(change_file)[i] + "\n" for i in range(
                len(open_lines(change_file)))).replace("_3 = 3", "_3 = 33")
        return determine(d, base, commit(d, files), state, CFG)


def determine_same_sha(recheck_pending, complete=True, findings=None):
    tmp, d, base, head, state = repo_with_review(
        [issue_finding()] if findings is None else findings)
    with tmp:
        return determine(d, base, head, dict(state, recheck_pending=recheck_pending,
                                             complete=complete), CFG)


def run_blocking_with_pending():
    prev = [dict(issue("i1"), pre="ask", located=True, recheck=True)]
    review = merge([{"index": 0, "status": "failed"}], {"block_severities": ["critical", "major"],
                   "block_confidence": 80, "min_confidence": 50}, prev, {}, False,
                   chunk_files={0: ["a"]})
    review["recheck_pending"] = pending_after({0: [{"id": "i1"}]}, [],
                                              [{"index": 0, "status": "failed"}])
    return review


class RecheckScopeTest(unittest.TestCase):
    def ids(self, s, key="recheck"):
        return [p["id"] for p in s["previous"] if p.get(key)]

    def test_null_check_above_the_issue_is_rechecked(self):
        """Flagged line unchanged, fix added above it."""
        s = determine_after_push(add_lines_at=1, issue_line=43)
        self.assertEqual(s["mode"], "incremental")
        self.assertTrue(any(p["id"] == "i1" and p["pre"] == "ask" and p["line"] == 44
                            for p in s["previous"]))
        self.assertEqual(self.ids(s), ["i1"])
        self.assertNotIn(44, s["changed"].get("a.cc", set()))

    def test_fix_in_another_file_still_rechecks(self):
        s = determine_after_push(change_file="b.cc", issue_file="a.cc", issue_line=43)
        self.assertIn("a.cc", {t["path"] for t in s["targets"]})
        self.assertEqual(s["mode"], "incremental")
        self.assertEqual([(p["id"], p["pre"]) for p in s["previous"]], [("i1", "ask")])
        self.assertEqual(self.ids(s), ["i1"])
        self.assertEqual(s["changed"], {"b.cc": {2, 3, 4}})  # recheck lines are not changed

    def test_a_squash_with_no_new_lines_rechecks_the_open_issue(self):
        tmp, d, base, head, state = repo_with_review([issue_finding()])
        with tmp:
            run(d, "reset", "-q", "--soft", base)
            run(d, "commit", "-q", "-m", "squashed")  # same tree, new sha
            s = determine(d, base, run(d, "rev-parse", "HEAD"), state, CFG)
        self.assertEqual((s["mode"], self.ids(s), s["changed"]), ("incremental", ["i1"], {}))
        self.assertEqual([t["path"] for t in s["targets"]], ["a.cc"])

    def test_same_sha_with_pending_recheck_is_not_unchanged(self):
        s = determine_same_sha(recheck_pending=["i1"])
        self.assertNotEqual(s["mode"], "unchanged")
        self.assertEqual((s["mode"], self.ids(s)), ("incremental", ["i1"]))

    def test_same_sha_rechecks_only_the_pending_ids(self):
        two = [issue_finding(id_="i1"), issue_finding("b.cc", 5, "i2")]
        s = determine_same_sha(recheck_pending=["i2"], findings=two)
        self.assertEqual([t["path"] for t in s["targets"]], ["b.cc"])
        self.assertEqual(self.ids(s), ["i2"])

    def test_same_sha_with_an_unknown_pending_id_still_runs(self):
        self.assertEqual(determine_same_sha(recheck_pending=["gone"])["mode"], "incremental")

    def test_same_sha_complete_with_nothing_pending_is_unchanged(self):
        self.assertEqual(determine_same_sha(recheck_pending=[])["mode"], "unchanged")

    def test_an_orphan_is_never_a_recheck_item(self):
        tmp, d, base, head, state = repo_with_review([issue_finding()])
        with tmp:
            lines = code("a", 60).splitlines(True)
            del lines[40:46]  # the flagged line goes away
            s = determine(d, base, commit(d, {"a.cc": "".join(lines)}), state, CFG)
        self.assertEqual([(p["pre"], p.get("recheck", False)) for p in s["previous"]],
                         [("orphan", False)])

    def test_a_deleted_file_is_not_a_recheck_item(self):
        tmp, d, base, head, state = repo_with_review([issue_finding()])
        with tmp:
            run(d, "rm", "-q", "a.cc")
            s = determine(d, base, commit(d, {}), state, CFG)
        self.assertEqual([(p["pre"], p.get("recheck", False)) for p in s["previous"]],
                         [("resolved", False)])

    def test_shown_lines_use_the_same_paths_as_the_previous_issues(self):
        """`parse_unified_diff` paths (chunks) match `previous[].file`, so the recheck lines of
        an issue count as shown for it and `resolved` is accepted."""
        big = code("a", 100)
        edited = big.replace("a_50 = 50", "a_50 = eval(x)")
        line50 = fp_line(edited.splitlines(), 50).hex()
        found = dict(issue_finding(line=50), anchor=line50)
        tmp, d, base, head, state = repo_with_review(
            [found], files={"a.cc": edited, "b.cc": code("b")}, base_files={"a.cc": big})
        with tmp:
            new_head = commit(d, {"b.cc": code("b").replace("_3 = 3", "_3 = 33")})
            s = determine(d, base, new_head, state, CFG)
        chunks, _, _ = build_chunks(s["targets"], CFG)
        by, left = assign_recheck(chunks, s["previous"], s["blobs"], CFG)
        shown = shown_lines(chunks, by)
        self.assertEqual((left, s["previous"][0]["file"], list(by)), ([], "a.cc", [0]))
        self.assertTrue(set(range(30, 71)) <= shown[0]["a.cc"])
        self.assertNotIn(10, shown[0]["a.cc"])
        out = {"summary": "", "findings": [], "incomplete_reason": None,
               "previous_findings": [{"id": "i1", "status": "resolved"}]}
        results = [{"index": c["index"], "status": "ok", "output": out} for c in chunks]
        rv = merge(results, CFG, s["previous"], s["changed"], False, closed=s["closed"],
                   chunk_files={c["index"]: c["files"] for c in chunks}, shown=shown)
        self.assertEqual([p["status"] for p in rv["previous"]], ["resolved"])

    def test_ids_pending_from_the_last_run_are_marked_on_a_new_head(self):
        two = [issue_finding(id_="i1"), issue_finding("b.cc", 5, "i2")]
        tmp, d, base, head, state = repo_with_review(two)
        with tmp:
            s = determine(d, base, commit(d, {"b.cc": code("b").replace("_3 = 3", "_3 = 33")}),
                          dict(state, recheck_pending=["i2"]), CFG)
        self.assertEqual([(p["id"], p.get("pending", False)) for p in s["previous"]],
                         [("i1", False), ("i2", True)])
        self.assertEqual(self.ids(s), ["i1", "i2"])  # all open issues are still rechecked

    def test_pending_does_not_change_the_verdict(self):
        review = run_blocking_with_pending()
        self.assertEqual((review["event"], review["recheck_pending"]),
                         ("REQUEST_CHANGES", ["i1"]))


if __name__ == "__main__":
    unittest.main()
