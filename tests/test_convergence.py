"""End to end: several pushes of one PR through the CLI, with a scripted fake model."""
import json
import os
import re

from review.render import parse_state
from tests.test_integration import BUGGY, IntegrationBase, git

BLOCK = "void f(int *%s) {\n  int n = len(%s);\n  %s[n] = 0;\n  use(%s);\n}\n"
PAD = "".join("static int pad%d = %d;\n" % (i, i * 7) for i in range(200))  # no rename pairing


def find(file, line, title="Out-of-bounds write", **extra):
    return dict({"file": file, "line": line, "severity": "major", "confidence": 90,
                 "category": "bug", "title": title, "detail": "d: %s" % title}, **extra)


def rule(when=None, findings=(), **kw):
    return dict(kw, when=when, findings=list(findings))


class Run:
    """What one run of the CLI posted and asked."""
    def __init__(self, t, before):
        post = t.gh.posted[-1]
        self.event, self.state = post["event"], parse_state(post["body"])
        self.inline = len(post["comments"])
        self.new_findings = int(re.findall(r"새 지적 (\d+)건", t.stdout)[-1])
        self.prompts = [c["stdin"] for c in t.fake_calls()[before:]] if t.has_log() else []
        self._t = t

    def issue(self, name):
        id_ = self._t.ids.get(name, name)
        return next(i for i in self.state["issues"] if i["id"] == id_)


class Scenario(IntegrationBase):
    def setUp(self):
        super().setUp()
        self.ids = {}
        self.script = os.path.join(self.bin_dir, "script.json")  # next to the call log

    def has_log(self):
        return hasattr(self, "log") and os.path.exists(self.log)

    def push(self, files, delete=()):
        for name in delete:
            git(self.R, "rm", "-q", name)
        self.gh.head = self.add_commit(files) if files else self._empty()

    def _empty(self):
        git(self.R, "commit", "-q", "--allow-empty", "-m", "m")
        return git(self.R, "rev-parse", "HEAD")

    def go(self, rules=(), *extra):
        with open(self.script, "w", encoding="utf-8") as f:
            json.dump(list(rules), f)
        before = len(self.fake_calls()) if self.has_log() else 0
        self.assertEqual(self.run_main("script", "--repo", self.R, "--pr", "1", *extra), 0)
        r = Run(self, before)
        if "i1" not in self.ids and r.state["issues"]:
            self.ids["i1"] = r.state["issues"][-1]["id"]  # the oldest issue
        return r

    def dismiss_all(self):
        for n, rv in enumerate(self.gh.reviews, 1):
            if rv["state"] != "DISMISSED":
                self.gh.add_dismissal(n, "alice")

    # --- scenarios -------------------------------------------------------------------

    def reworded(self, severity, same_as=False):
        """The model raises one issue in new words on a neighboring line on every push."""
        runs = [self.go([rule("- src/k.cu\n", [find("src/k.cu", 2, severity=severity)])])]
        for n, extra in enumerate(("int z2;\n", "int z2;\nint z3;\n"), 1):
            self.push({"src/k.cu": BUGGY + extra})
            line = 3 + n  # 4, then 5: within 3 lines of the region at line 2
            more = {"same_as": self.ids["i1"]} if same_as else {}
            runs.append(self.go([rule("- src/k.cu\n", [find("src/k.cu", line, "Reworded %d" % n,
                                                             severity=severity, **more)])]))
        return runs

    def test_reworded_non_blocking_reraise_converges(self):
        runs = self.reworded("minor")
        self.assertEqual([r.inline for r in runs], [1, 0, 0])
        self.assertEqual([r.new_findings for r in runs], [1, 0, 0])
        self.assertEqual(len(runs[-1].state["issues"]), 1)
        self.assertEqual(runs[-1].event, "APPROVE")

    def test_reworded_blocking_reraise_converges_only_with_same_as(self):
        runs = self.reworded("major", same_as=True)
        self.assertEqual([r.new_findings for r in runs], [1, 0, 0])
        self.assertEqual(len(runs[-1].state["issues"]), 1)
        self.assertEqual(runs[-1].event, "REQUEST_CHANGES")  # still blocked by the one issue

    def test_reworded_blocking_reraise_without_same_as_is_raised_again(self):
        """Rev 9's accepted cost: position never merges a blocking finding."""
        runs = self.reworded("major")
        self.assertEqual([r.new_findings for r in runs], [1, 1, 1])
        self.assertEqual([r.event for r in runs], ["REQUEST_CHANGES"] * 3)
        self.assertEqual(len(runs[-1].state["issues"]), 3)
        self.assertIn("기존 이슈 근처의 새 지적: src/k.cu:5 (이슈 ", self.stdout)

    def test_codex1_a_distinct_defect_next_to_an_open_issue_is_its_own_issue(self):
        """Open major A at 50; a distinct major/95 B at 52 without `same_as`; then A is
        fixed and B is not reported again: the run must not approve."""
        code = ["int v%d;\n" % n for n in range(200)]
        self.push({"src/r.cu": "".join(code)})
        self.go([rule("- src/r.cu\n", [find("src/r.cu", 50, "A")])])
        code[51] = "int b52;\n"
        self.push({"src/r.cu": "".join(code)})
        r1 = self.go([rule("- src/r.cu\n", [find("src/r.cu", 52, "B", confidence=95)])])
        self.assertEqual((r1.new_findings, r1.event), (1, "REQUEST_CHANGES"))
        b = next(i for i in r1.state["issues"] if i["line"] == 52)
        self.assertEqual((b["status"], b["blocking"]), ("open", True))
        code[49] = "int fixed;\n"
        self.push({"src/r.cu": "".join(code)})
        r2 = self.go([rule("- src/r.cu\n", resolve=[self.ids["i1"]])])
        self.assertEqual((r2.issue("i1")["status"], r2.issue(b["id"])["status"]),
                         ("resolved", "open"))
        self.assertEqual(r2.event, "REQUEST_CHANGES")

    def test_folded_findings_are_listed_in_the_body(self):
        self.go([rule("- src/k.cu\n", [find("src/k.cu", 2)])])
        self.push({"src/k.cu": BUGGY + "int z2;\n"})
        r = self.go([rule("- src/k.cu\n", [find("src/k.cu", 4, "Reworded", severity="minor")])])
        post = self.gh.posted[-1]
        self.assertEqual((r.inline, r.event), (0, "REQUEST_CHANGES"))
        self.assertIn("### 기존 이슈에 합쳐진 지적 (1건)", post["body"])
        self.assertIn("`src/k.cu:4` 경미 Reworded → 이슈 %s: d: Reworded" % self.ids["i1"],
                      post["body"])

    def test_large_block_inserted_in_a_closed_region_keeps_new_findings(self):
        r = self.go([rule("- src/k.cu\n", [find("src/k.cu", 2)])])
        self.dismiss_all()  # waived
        self.push({"src/k.cu": "int k(int *p) {\n" + "".join("  int w%d;\n" % n for n in range(11))
                   + "}\n"})
        widened = self.go().issue("i1")  # line 2 is rewritten into 11 lines: 5 of them count
        self.assertEqual((widened["status"], widened["lo"], widened["hi"]), ("waived", 2, 7))
        lines = BUGGY.split("\n")[:1] + ["  int w%d;" % n for n in range(11)] + ["}", ""]
        body = lines[:5] + ["int a%d;" % n for n in range(1000)] + lines[5:]
        self.push({"src/k.cu": "\n".join(body)})
        last = self.go([rule("- src/k.cu\n", [find("src/k.cu", 600, "New in inserted code")])])
        self.assertEqual(last.new_findings, 1)
        i1 = last.issue("i1")
        self.assertEqual((i1["lo"], i1["hi"]), (2, 7))  # the inserted code did not widen it

    def test_reworded_reraise_on_a_rewritten_block_is_the_same_issue(self):
        self.go([rule("- src/k.cu\n", [find("src/k.cu", 2)])])
        body = "".join("  int w%d = p[%d];\n" % (n, n) for n in range(11))
        self.push({"src/k.cu": "int k(int *p) {\n" + body + "}\n"})
        r = self.go([rule("- src/k.cu\n", [find("src/k.cu", 9, "Same defect, reworded",
                                                 severity="minor")])])
        self.assertEqual(r.new_findings, 0)
        self.assertEqual((r.issue("i1")["lo"], r.issue("i1")["hi"]), (2, 7))

    def test_waiver_does_not_cross_to_another_variable(self):
        """A waived 5-line block is deleted; the same block on another variable lands in another file."""
        self.push({"src/a.cu": BLOCK % ("x", "x", "x", "x")})
        self.go([rule("- src/a.cu\n", [find("src/a.cu", 3)])])
        self.dismiss_all()
        self.push({"src/b.cu": BLOCK % ("y", "y", "y", "y") + PAD}, delete=["src/a.cu"])
        last = self.go([rule("- src/b.cu\n", [find("src/b.cu", 3)])])
        self.assertEqual(last.new_findings, 1)
        self.assertEqual(last.issue("i1")["status"], "waived")

    # a 40-line function with a defect, moved out of src/m.cu into the new file src/n.cu
    def move(self, same_as):
        func = "void g() {\n" + "".join("  step_%d();\n" % n for n in range(40)) + "}\n"
        head = "".join("int head%d;\n" % n for n in range(10))
        tail = "".join("int tail%d = %d;\n" % (n, n * 3) for n in range(60))
        self.push({"src/m.cu": head + func + tail})
        self.go([rule("- src/m.cu\n", [find("src/m.cu", 20)])])
        self.dismiss_all()
        self.push({"src/m.cu": head + tail, "src/n.cu": func})
        extra = {"same_as": self.ids["i1"]} if same_as else {}
        return self.go([rule("- src/n.cu\n", [find("src/n.cu", 10, "Moved", **extra)])])

    def test_moved_function_with_same_as_stays_waived(self):
        r = self.move(same_as=True)
        self.assertEqual((r.new_findings, r.issue("i1")["status"]), (0, "waived"))
        self.assertEqual((r.issue("i1")["file"], r.issue("i1")["line"]), ("src/n.cu", 10))

    def test_moved_function_without_same_as_is_raised_once_more(self):
        self.assertEqual(self.move(same_as=False).new_findings, 1)

    def test_short_move_to_another_chunk_links_through_the_orphan_list(self):
        self.push({"src/a.cu": BLOCK % ("x", "x", "x", "x"), "src/c.cu": "int c;\n" * 3})
        self.go([rule("- src/a.cu\n", [find("src/a.cu", 3)])])
        self.dismiss_all()
        dest = BLOCK % ("x", "x", "x", "x") + "".join("int own%d = %d;\n" % (n, n) for n in range(20))
        self.push({"src/b.cu": dest, "src/c.cu": "int c2;\n" * 3}, delete=["src/a.cu"])
        link = find("src/b.cu", 3, "Moved", same_as=self.ids["i1"])
        r = self.go([rule("- src/b.cu\n", [link])], "--set", "chunk_max_tokens=100")
        chunk = next(p for p in r.prompts if "- src/b.cu\n" in p)
        self.assertGreater(len(r.prompts), 1)  # the files are in different chunks
        self.assertNotIn("- src/c.cu\n", chunk)
        self.assertIn(self.ids["i1"], chunk)
        self.assertEqual(r.new_findings, 0)

    # a function with a defect at line 3 that a push moves below the rest of the file
    FUNC = "void g(int *p) {\n  int n = size(p);\n  p[n] = 0;\n  use(p);\n  done(p);\n}\n"
    REST = "".join("int h%d(void) { return %d; }\n" % (n, n * 3) for n in range(30))

    def reorder(self):
        self.push({"src/m.cu": self.FUNC + self.REST})
        first = self.go([rule("- src/m.cu\n", [find("src/m.cu", 3)])])
        self.assertEqual(first.event, "REQUEST_CHANGES")
        self.push({"src/m.cu": self.REST + self.FUNC})

    def test_in_file_move_with_a_silent_model_keeps_the_issue_open_at_the_new_line(self):
        self.reorder()
        r = self.go([rule()])
        self.assertEqual(r.event, "REQUEST_CHANGES")
        i1 = r.issue("i1")
        self.assertEqual((i1["status"], i1["line"], i1["lo"], i1["hi"]), ("open", 33, 33, 33))
        self.assertFalse(i1.get("orphan"))
        self.assertEqual(self.gh.dismissed, [])
        self.assertIn("이슈 %s, " % self.ids["i1"], r.prompts[0])  # rechecked as usual

    def test_in_file_move_with_trailing_spaces_keeps_the_issue_open_and_rechecked(self):
        self.push({"src/m.cu": self.FUNC + self.REST})
        self.go([rule("- src/m.cu\n", [find("src/m.cu", 3)])])
        spaced = "".join(ln + "  \n" for ln in self.FUNC.split("\n")[:-1])
        self.push({"src/m.cu": self.REST + spaced})
        r = self.go([rule()])
        i1 = r.issue("i1")
        self.assertEqual((r.event, i1["status"], i1["line"], bool(i1.get("orphan"))),
                         ("REQUEST_CHANGES", "open", 33, False))
        self.assertIn("이슈 %s, " % self.ids["i1"], r.prompts[0])

    def test_in_file_move_can_be_resolved_by_the_recheck(self):
        self.reorder()
        r = self.go([rule(resolve_rechecked=True)])
        self.assertEqual((r.issue("i1")["status"], r.event), ("resolved", "APPROVE"))

    def test_in_file_move_of_a_fixed_line_is_an_orphan_and_resolves(self):
        self.push({"src/m.cu": self.FUNC + self.REST})
        self.go([rule("- src/m.cu\n", [find("src/m.cu", 3)])])
        self.push({"src/m.cu": self.REST + self.FUNC.replace("p[n]", "p[n - 1]")})
        r = self.go([rule()])
        self.assertEqual((r.issue("i1")["status"], r.event), ("resolved", "APPROVE"))

    def test_in_file_move_does_not_carry_a_waiver(self):
        self.push({"src/m.cu": self.FUNC + self.REST})
        self.go([rule("- src/m.cu\n", [find("src/m.cu", 3)])])
        self.dismiss_all()
        self.push({"src/m.cu": self.REST + self.FUNC})
        r = self.go([rule()])
        self.assertEqual((r.issue("i1")["status"], r.issue("i1").get("orphan")), ("waived", True))

    def test_skipped_chunk_cannot_close_and_the_retry_reports(self):
        first = self.go([rule("- src/k.cu\n", [find("src/k.cu", 2)])])
        self.assertEqual(first.event, "REQUEST_CHANGES")
        # src/a.cu sorts first and takes the only chunk; src/k.cu (the fix) is skipped
        self.push({"src/a.cu": "int a;\n" * 3, "src/k.cu": "int k(int *p) {\n  return p[0];\n}\n"})
        claim = rule(None, resolve=[self.ids["i1"]])
        r1 = self.go([claim], "--set", "max_chunks=1", "--set", "chunk_max_tokens=45")
        self.assertEqual(r1.issue("i1")["status"], "open")
        self.assertFalse(r1.state["complete"])
        r2 = self.go([rule("- src/k.cu\n", resolve=[self.ids["i1"]])])
        self.assertEqual(r2.issue("i1")["status"], "resolved")
        self.assertEqual(r2.event, "APPROVE")

    def test_recheck_pending_rerun_on_the_same_sha_rechecks_the_left_out_issue(self):
        code = "".join("int v%d;\n" % n for n in range(100))
        self.push({"src/r.cu": code})
        both = [find("src/r.cu", 10, "first"), find("src/r.cu", 90, "second")]
        self.go([rule("- src/r.cu\n", both)])
        self.push({"src/r.cu": code + "int more;\n"})
        # 41 lines of context are about 400 chars: only one issue fits the recheck budget
        cap = ("--set", "recheck_max_chars=500")
        r1 = self.go([rule("- src/r.cu\n", resolve_rechecked=True)], *cap)
        left = r1.state["recheck_pending"]
        self.assertEqual(len(left), 1)
        self.assertEqual(sorted(i["status"] for i in r1.state["issues"]), ["open", "resolved"])
        r2 = self.go([rule("- src/r.cu\n", resolve_rechecked=True)], *cap)
        self.assertEqual(r2.state["sha"], r1.state["sha"])
        self.assertEqual(len(r2.prompts), 1)
        self.assertIn("이슈 %s, " % left[0], r2.prompts[0])
        self.assertEqual(r2.state["recheck_pending"], [])
        self.assertEqual([i["status"] for i in r2.state["issues"]], ["resolved", "resolved"])
        self.assertEqual(r2.event, "APPROVE")

    def test_resolved_refused_because_of_another_chunk_is_retried_on_the_same_sha(self):
        """A file split over two chunks: the one with the recheck says resolved, the other is not ok."""
        code = ["static int variable_number_%03d_of_this_long_list;\n" % n for n in range(200)]
        self.push({"src/r.cu": "".join(code)})
        first = self.go([rule("- src/r.cu\n", [find("src/r.cu", 10)])])
        self.assertEqual(first.event, "REQUEST_CHANGES")
        code[9], code[189] = "int fixed;\n", "int other;\n"
        self.push({"src/r.cu": "".join(code)})
        cap = ("--set", "chunk_max_tokens=150")  # one hunk fits a chunk, two do not
        r1 = self.go([rule("int other;", unreviewed=True), rule(resolve_rechecked=True)], *cap)
        self.assertGreater(len(r1.prompts), 1)
        self.assertEqual(r1.issue("i1")["status"], "open")
        self.assertEqual(r1.state["recheck_pending"], [self.ids["i1"]])
        r2 = self.go([rule(resolve_rechecked=True)], *cap)
        self.assertEqual(r2.state["sha"], r1.state["sha"])
        self.assertTrue(any("이슈 %s, " % self.ids["i1"] in p for p in r2.prompts))
        self.assertEqual(r2.issue("i1")["status"], "resolved")
        self.assertEqual((r2.state["recheck_pending"], r2.event), ([], "APPROVE"))

    def test_resolved_refused_because_part_of_the_file_was_skipped_is_retried(self):
        """A file split over two chunks; `max_chunks` drops the second one."""
        code = ["static int variable_number_%03d_of_this_long_list;\n" % n for n in range(200)]
        self.push({"src/r.cu": "".join(code)})
        self.go([rule("- src/r.cu\n", [find("src/r.cu", 10)])])
        code[9], code[189] = "int fixed;\n", "int other;\n"
        self.push({"src/r.cu": "".join(code)})
        cap = ("--set", "chunk_max_tokens=150", "--set", "max_chunks=1")
        r1 = self.go([rule(resolve_rechecked=True)], *cap)
        self.assertEqual(len(r1.prompts), 1)
        self.assertEqual(r1.issue("i1")["status"], "open")
        self.assertEqual(r1.state["recheck_pending"], [self.ids["i1"]])
        r2 = self.go([rule(resolve_rechecked=True)], *cap)
        self.assertEqual(r2.state["sha"], r1.state["sha"])
        self.assertTrue(any("이슈 %s, " % self.ids["i1"] in p for p in r2.prompts))
        self.assertEqual(r2.issue("i1")["status"], "resolved")
        self.assertEqual((r2.state["recheck_pending"], r2.event), ([], "APPROVE"))

    def test_a_line_longer_than_a_chunk_is_cut_and_the_same_sha_stops(self):
        """Codex: the row was dropped as truncated, never seen, and every same-sha run
        repeated. Now it is cut to fit, counts as seen, and is a permanent gap."""
        code = ["int v%d;\n" % n for n in range(300)]
        self.push({"src/r.cu": "".join(code)})
        self.go([rule("- src/r.cu\n", [find("src/r.cu", 10)])])
        code[9] = "int fixed;\n"
        code[150] = "int sum = %s1;\n" % ("1 + " * 1000)  # one line over the chunk limit
        self.push({"src/r.cu": "".join(code)})
        cap = ("--set", "chunk_max_tokens=400")
        r1 = self.go([rule(resolve_rechecked=True)], *cap)
        body = self.gh.posted[-1]["body"]
        self.assertIn("잘린 파일 0개", self.stdout)
        self.assertIn("줄이 너무 길어 일부만 검토함: src/r.cu:151", self.stdout)
        self.assertTrue(any("int sum = 1 + 1 + " in p and "줄이 너무 길어 잘림" in p
                            for p in r1.prompts))
        self.assertEqual((r1.issue("i1")["status"], r1.state["recheck_pending"]), ("resolved", []))
        self.assertTrue(r1.state["complete"])
        self.assertEqual(set(r1.state["cut"]), {"src/r.cu"})
        self.assertEqual(r1.event, "COMMENT")
        self.assertIn("줄이 너무 길어 일부만 검토한 파일: `src/r.cu`", body)
        posts = len(self.gh.posted)
        self.assertEqual(self.run_main("script", "--repo", self.R, "--pr", "1", *cap), 0)
        self.assertIn("새로 리뷰할 변경 없음", self.stdout)  # the same sha stops
        self.assertEqual(len(self.gh.posted), posts)
        code[250] = "int other;\n"  # the long line is still in the head: still no approval
        self.push({"src/r.cu": "".join(code)})
        r3 = self.go([rule()], *cap)
        self.assertTrue(all("int sum = 1 + " not in p for p in r3.prompts))
        self.assertEqual((r3.event, r3.state["cut"]), ("COMMENT", r1.state["cut"]))
        self.assertIn("줄이 너무 길어 일부만 검토함: src/r.cu", self.stdout)
        code[150] = "int sum = 2;\n"  # shortened: reviewed whole
        self.push({"src/r.cu": "".join(code)})
        r4 = self.go([rule()], *cap)
        self.assertEqual(r4.event, "APPROVE")
        self.assertNotIn("cut", r4.state)
        self.assertNotIn("줄이 너무 길어", self.stdout)

    def test_a_cut_line_in_a_renamed_file_still_blocks_approval(self):
        code = ["int v%d;\n" % n for n in range(300)]
        code[150] = "int sum = %s1;\n" % ("1 + " * 1000)
        self.push({"src/r.cu": "".join(code)})
        cap = ("--set", "chunk_max_tokens=400")
        self.assertEqual(self.go([rule()], *cap).event, "COMMENT")
        git(self.R, "mv", "src/r.cu", "src/q.cu")
        code[250] = "int other;\n"
        self.push({"src/q.cu": "".join(code)})
        r = self.go([rule()], *cap)
        self.assertEqual((r.event, set(r.state["cut"])), ("COMMENT", {"src/q.cu"}))

    # a defect in the last line of a file; the fix reverts the file and renames it, so the PR
    # diff of the file has no hunk: the PR no longer changes the file
    def rename_after_revert(self, *others):
        pad = "".join("int pad%d = %d;\n" % (n, n) for n in range(40))
        self.gh.base = self.add_commit({"src/z.cu": pad + "int ok = 1;\n"})
        self.gh.head = self.add_commit({"src/z.cu": pad + "int bug = 1;\n"})
        self.assertEqual(self.go([rule("- src/z.cu\n", [find("src/z.cu", 41)])]).event,
                         "REQUEST_CHANGES")
        git(self.R, "mv", "src/z.cu", "src/y.cu")
        self.push(dict({"src/y.cu": pad + "int ok = 1;\n"}, **dict(others)))

    def assert_reverted(self, r):
        self.assertEqual(r.state["recheck_pending"], [])
        self.assertEqual((r.issue("i1")["status"], r.event), ("resolved", "APPROVE"))
        self.assertIn("PR 변경이 되돌려짐", self.gh.posted[-1]["body"])
        self.assertNotIn("다시 확인할 이슈", self.stdout)

    def test_a_file_without_hunks_resolves_its_issue_as_reverted(self):
        self.rename_after_revert()
        first = self.go()
        self.assert_reverted(first)
        self.assertEqual(first.prompts, [])
        posts = len(self.gh.posted)
        self.assertEqual(self.run_main("script", "--repo", self.R, "--pr", "1"), 0)
        self.assertIn("새로 리뷰할 변경 없음", self.stdout)  # the same sha is settled
        self.assertEqual(len(self.gh.posted), posts)

    def test_a_file_without_hunks_resolves_when_other_files_are_reviewed(self):
        self.rename_after_revert(("src/other.cu", "int other = 1;\n"))
        r = self.go([rule()])
        self.assert_reverted(r)
        self.assertEqual(len(r.prompts), 1)

    def test_a_recheck_larger_than_the_budget_is_served_with_less_context(self):
        code = ["static int variable_number_%03d_of_this_long_list;\n" % n for n in range(100)]
        self.push({"src/r.cu": "".join(code)})
        self.go([rule("- src/r.cu\n", [find("src/r.cu", 50)])])
        self.push({"src/r.cu": "".join(code) + "int more;\n"})
        cap = ("--set", "recheck_max_chars=200")  # under 41 lines of context
        r1 = self.go([rule(resolve_rechecked=True)], *cap)
        self.assertIn("이슈 %s, src/r.cu 49-51줄:" % self.ids["i1"], r1.prompts[0])
        self.assertEqual((r1.state["recheck_pending"], r1.issue("i1")["status"], r1.event),
                         ([], "resolved", "APPROVE"))

    # --- a new defect near a closed issue (Codex review F1) --------------------------

    def near_resolved(self):
        code = ["int v%d;\n" % n for n in range(200)]
        self.push({"src/r.cu": "".join(code)})
        self.go([rule("- src/r.cu\n", [find("src/r.cu", 100, "Minor", severity="minor")])])
        code[99] = "int fixed;\n"
        self.push({"src/r.cu": "".join(code)})
        self.assertEqual(self.go([rule(resolve_rechecked=True)]).issue("i1")["status"], "resolved")
        code[101] = "int changed;\n"
        self.push({"src/r.cu": "".join(code)})

    def test_a_more_severe_defect_near_a_resolved_issue_is_raised_and_blocks(self):
        self.near_resolved()
        r = self.go([rule("- src/r.cu\n", [find("src/r.cu", 102, "Other", severity="critical")])])
        self.assertEqual((r.new_findings, r.inline, r.event), (1, 1, "REQUEST_CHANGES"))
        self.assertIn("해결/면제된 이슈 근처의 새 지적: src/r.cu:102 (이슈 %s)" % self.ids["i1"],
                      self.stdout)

    def test_an_as_severe_defect_near_a_resolved_issue_is_posted_without_blocking(self):
        self.near_resolved()
        r = self.go([rule("- src/r.cu\n", [find("src/r.cu", 102, "Other", severity="minor")])])
        self.assertEqual((r.new_findings, r.inline, r.event), (1, 1, "APPROVE"))
        self.assertIn("해결/면제된 이슈 %s 근처의 새 지적" % self.ids["i1"],
                      self.gh.posted[-1]["comments"][0]["body"])
        self.assertIn("해결/면제된 이슈 근처의 새 지적: src/r.cu:102 (이슈 %s)" % self.ids["i1"],
                      self.stdout)

    def test_a_critical_next_to_an_open_minor_is_raised_once_and_then_converges(self):
        code = ["int v%d;\n" % n for n in range(200)]
        self.push({"src/r.cu": "".join(code)})
        self.go([rule("- src/r.cu\n", [find("src/r.cu", 100, "Minor", severity="minor")])])
        code[101] = "int changed;\n"
        self.push({"src/r.cu": "".join(code)})
        crit = rule("- src/r.cu\n", [find("src/r.cu", 102, "Crit", severity="critical")])
        r1 = self.go([crit])
        self.assertEqual((r1.new_findings, r1.event), (1, "REQUEST_CHANGES"))
        self.assertIn("기존 이슈 근처의 새 지적: src/r.cu:102 (이슈 %s)" % self.ids["i1"],
                      self.stdout)
        crit_id = next(i["id"] for i in r1.state["issues"] if i["line"] == 102)
        code[102] = "int changed2;\n"
        self.push({"src/r.cu": "".join(code)})
        again = rule("- src/r.cu\n", [find("src/r.cu", 103, "Crit again", severity="critical",
                                           same_as=crit_id)])
        r2 = self.go([again])
        self.assertEqual((r2.new_findings, r2.inline, r2.event), (0, 0, "REQUEST_CHANGES"))

    def test_a_defect_next_to_an_issue_resolved_in_the_run_blocks_once(self):
        """Review I3 (H1): critical A is fixed and another critical sits two lines below;
        the re-raise names the new issue in `same_as`."""
        code = ["int v%d;\n" % n for n in range(200)]
        self.push({"src/r.cu": "".join(code)})
        self.go([rule("- src/r.cu\n", [find("src/r.cu", 100, "Crit", severity="critical")])])
        code[99], code[101] = "int fixed;\n", "int other;\n"
        self.push({"src/r.cu": "".join(code)})
        other = find("src/r.cu", 102, "Other", severity="critical")
        r = self.go([rule("- src/r.cu\n", [other], resolve=[self.ids["i1"]])])
        self.assertEqual((r.new_findings, r.inline, r.event), (1, 1, "REQUEST_CHANGES"))
        self.assertEqual(sorted((i["line"], i["status"]) for i in r.state["issues"]),
                         [(100, "resolved"), (102, "open")])
        posts = len(self.gh.posted)
        self.assertEqual(self.run_main("script", "--repo", self.R, "--pr", "1"), 0)
        self.assertIn("새로 리뷰할 변경 없음", self.stdout)  # the same sha is settled
        self.assertEqual(len(self.gh.posted), posts)
        code[102] = "int other2;\n"
        self.push({"src/r.cu": "".join(code)})
        other_id = next(i["id"] for i in r.state["issues"] if i["line"] == 102)
        again = find("src/r.cu", 103, "Other again", severity="critical", same_as=other_id)
        r = self.go([rule("- src/r.cu\n", [again])])
        self.assertEqual((r.new_findings, r.inline, r.event), (0, 0, "REQUEST_CHANGES"))
        self.assertEqual(len(r.state["issues"]), 2)

    def test_a_reraise_next_to_a_waived_critical(self):
        """Waived critical at 100, open minor at 103, critical re-raises at 101: with `same_as`
        it never blocks; without it, it is new and blocks (rev 9's accepted cost)."""
        code = ["int v%d;\n" % n for n in range(200)]
        self.push({"src/r.cu": "".join(code)})
        self.go([rule("- src/r.cu\n", [find("src/r.cu", 100, "Crit", severity="critical")])])
        self.dismiss_all()
        code[102] = "int c103;\n"
        self.push({"src/r.cu": "".join(code)})
        minor = self.go([rule("- src/r.cu\n", [find("src/r.cu", 103, "Minor", severity="minor")])])
        self.assertEqual((minor.new_findings, minor.event), (1, "APPROVE"))
        events, new = [], []
        for n in range(3):
            code[100] = "int c101_%d;\n" % n
            self.push({"src/r.cu": "".join(code)})
            r = self.go([rule("- src/r.cu\n",
                              [find("src/r.cu", 101, "Crit again %d" % n, severity="critical",
                                    same_as=self.ids["i1"])])])
            events.append(r.event)
            new.append(r.new_findings)
            self.dismiss_all()
        self.assertEqual((events, new), (["APPROVE"] * 3, [0, 0, 0]))
        code[100] = "int c101_x;\n"
        self.push({"src/r.cu": "".join(code)})
        r = self.go([rule("- src/r.cu\n", [find("src/r.cu", 101, "Crit reworded",
                                                severity="critical")])])
        self.assertEqual((r.new_findings, r.event), (1, "REQUEST_CHANGES"))
        self.assertIn("해결/면제된 이슈 근처의 새 지적: src/r.cu:101 (이슈 %s)" % self.ids["i1"],
                      self.stdout)

    # --- `same_as` naming a less severe issue (dedupe hole 2) -------------------------

    def resolved_minor(self):
        """Minor C at line 100, resolved. Returns the file lines."""
        code = ["int v%d;\n" % n for n in range(200)]
        self.push({"src/r.cu": "".join(code)})
        self.go([rule("- src/r.cu\n", [find("src/r.cu", 100, "Minor", severity="minor")])])
        code[99] = "int fixed;\n"
        self.push({"src/r.cu": "".join(code)})
        self.assertEqual(self.go([rule(resolve_rechecked=True)]).issue("i1")["status"], "resolved")
        return code

    def crit_same_as_c(self, code, line, resolve=()):
        code[line - 1] = "int c%d;\n" % line
        self.push({"src/r.cu": "".join(code)})
        f = find("src/r.cu", line, "Crit at %d" % line, severity="critical",
                 same_as=self.ids["i1"])
        return self.go([rule("- src/r.cu\n", [f], resolve=list(resolve))])

    def via_c(self, r):
        return [i for i in r.state["issues"] if i.get("via") == self.ids["i1"]]

    def test_same_as_to_a_less_severe_closed_issue_does_not_drop(self):
        code = self.resolved_minor()
        r = self.crit_same_as_c(code, 110)
        self.assertEqual((r.new_findings, r.inline, r.event), (1, 1, "REQUEST_CHANGES"))
        d = self.via_c(r)
        self.assertEqual([(i["line"], i["severity"], i["status"]) for i in d],
                         [(110, "critical", "open")])
        self.assertIn("same_as 무시: 지정한 이슈보다 심각함 — src/r.cu:110 (이슈 %s)"
                      % self.ids["i1"], self.stdout)

    def test_a_repeated_more_severe_same_as_converges(self):
        self.five_runs()

    def five_runs(self):
        """Focus 4: the model names the resolved minor C for a critical over five runs."""
        code = self.resolved_minor()
        runs = [self.crit_same_as_c(code, 102)]
        x = self.via_c(runs[0])
        self.assertEqual([i["line"] for i in x], [102])
        runs.append(self.crit_same_as_c(code, 106))
        # Codex #3: X reported resolved while the same run links a critical to it: the link wins
        runs.append(self.crit_same_as_c(code, 110, resolve=[x[0]["id"]]))
        self.assertEqual(runs[-1].issue(x[0]["id"])["status"], "open")
        self.assertIn("같은 문제가 다시 지적되어 해결 보고를 무시: src/r.cu:110 (이슈 %s)"
                      % x[0]["id"], self.stdout)
        code[110] = "int fixed110;\n"
        self.push({"src/r.cu": "".join(code)})
        runs.append(self.go([rule("- src/r.cu\n", resolve=[x[0]["id"]])]))
        runs += [self.crit_same_as_c(code, line) for line in (114, 117)]
        self.assertEqual([r.new_findings for r in runs], [1, 0, 0, 0, 0, 0])
        self.assertEqual([r.event for r in runs][1:],
                         ["REQUEST_CHANGES"] * 2 + ["APPROVE"] * 3)
        self.assertEqual([len(self.via_c(r)) for r in runs], [1] * 6)
        self.assertEqual(runs[-1].issue(x[0]["id"])["status"], "resolved")
        return code, runs

    def test_drift_beyond_the_family_creates_one_issue_per_15_lines(self):
        code, _ = self.five_runs()
        far = self.crit_same_as_c(code, 118)  # 16 lines from X, 18 from C
        self.assertEqual((far.new_findings, far.event), (1, "REQUEST_CHANGES"))
        self.assertEqual(sorted(i["line"] for i in self.via_c(far)), [102, 118])
        near = self.crit_same_as_c(code, 122)  # 4 lines from X2
        self.assertEqual(near.new_findings, 0)
        self.assertEqual(sorted(i["line"] for i in self.via_c(near)), [102, 118])

    # --- a recheck-only file split by the chunk limit (dedupe hole 3) -------------------

    CAP = ("--set", "chunk_max_tokens=50", "--set", "max_chunks=1")

    def two_issues(self, line190=None):
        """src/r.cu fully reviewed with open issues at lines 10 and 190; then a head that does
        not touch the file. Returns the file lines."""
        code = ["static int variable_number_%03d_of_this_long_list;\n" % n for n in range(200)]
        if line190 is not None:
            code[189] = line190 + "\n"
        self.push({"src/r.cu": "".join(code)})
        first = self.go([rule("- src/r.cu\n", [find("src/r.cu", 10, "first"),
                                               find("src/r.cu", 190, "second")])])
        for i in first.state["issues"]:
            self.ids["L%d" % i["line"]] = i["id"]
        self.push({})
        return code

    def rechecked(self, r):
        return [m for p in r.prompts for m in re.findall(r"^이슈 (\w+), ", p, re.M)]

    def test_recheck_only_file_split_by_max_chunks_progresses(self):
        """Focus 5: both items are in the one chunk; the other hunk of the file is skipped."""
        self.two_issues()
        r = self.go([rule(resolve_rechecked=True)], *self.CAP)
        self.assertIn("생략 파일 1개", self.stdout)
        self.assertEqual(len(r.prompts), 1)
        self.assertEqual(sorted(self.rechecked(r)), sorted([self.ids["L10"], self.ids["L190"]]))
        self.assertEqual([i["status"] for i in r.state["issues"]], ["resolved", "resolved"])
        self.assertEqual((r.state["recheck_pending"], r.state["complete"], r.event),
                         ([], True, "APPROVE"))
        posts = len(self.gh.posted)
        self.assertEqual(self.run_main("script", "--repo", self.R, "--pr", "1", *self.CAP), 0)
        self.assertIn("새로 리뷰할 변경 없음", self.stdout)  # the same sha is settled
        self.assertEqual(len(self.gh.posted), posts)

    def test_a_skipped_file_with_new_lines_still_refuses_resolved(self):
        code = self.two_issues()
        code[12], code[186] = "int new_a;\n", "int new_b;\n"
        self.push({"src/r.cu": "".join(code)})
        r = self.go([rule(resolve_rechecked=True)], *self.CAP)
        self.assertIn("생략 파일 1개", self.stdout)
        self.assertEqual(sorted(self.rechecked(r)), sorted([self.ids["L10"], self.ids["L190"]]))
        self.assertEqual([i["status"] for i in r.state["issues"]], ["open", "open"])
        self.assertEqual(sorted(r.state["recheck_pending"]),
                         sorted([self.ids["L10"], self.ids["L190"]]))
        self.assertEqual((r.state["complete"], r.event), (False, "REQUEST_CHANGES"))

    def test_recheck_only_item_left_out_stays_pending(self):
        self.two_issues()
        cap = self.CAP + ("--set", "recheck_max_chars=2500")  # one window of ~31 lines fits
        r1 = self.go([rule(resolve_rechecked=True)], *cap)
        self.assertEqual(self.rechecked(r1), [self.ids["L10"]])
        self.assertEqual(r1.state["recheck_pending"], [self.ids["L190"]])
        self.assertEqual((r1.issue("L10")["status"], r1.issue("L190")["status"]),
                         ("resolved", "open"))
        self.assertEqual((r1.state["complete"], r1.event), (False, "REQUEST_CHANGES"))
        r2 = self.go([rule(resolve_rechecked=True)], *cap)
        self.assertEqual(r2.state["sha"], r1.state["sha"])
        self.assertEqual(self.rechecked(r2), [self.ids["L190"]])
        self.assertEqual(r2.issue("L190")["status"], "resolved")
        self.assertEqual((r2.state["recheck_pending"], r2.state["complete"], r2.event),
                         ([], True, "APPROVE"))

    def test_recheck_only_file_with_a_failed_chunk_keeps_its_ids_pending(self):
        self.two_issues()
        cap = ("--set", "chunk_max_tokens=50")  # the two hunks are two chunks, both run
        both = sorted([self.ids["L10"], self.ids["L190"]])
        holder = rule("이슈 %s, " % self.ids["L10"], resolve_rechecked=True)
        r1 = self.go([holder, rule(unreviewed=True)], *cap)
        self.assertGreater(len(r1.prompts), 1)
        self.assertEqual(sorted(self.rechecked(r1)), both)
        self.assertEqual([i["status"] for i in r1.state["issues"]], ["open", "open"])
        self.assertEqual(sorted(r1.state["recheck_pending"]), both)
        self.assertFalse(r1.state["complete"])
        r2 = self.go([rule(resolve_rechecked=True)], *cap)
        self.assertEqual(r2.state["sha"], r1.state["sha"])
        self.assertEqual(sorted(self.rechecked(r2)), both)
        self.assertEqual([i["status"] for i in r2.state["issues"]], ["resolved", "resolved"])
        self.assertEqual((r2.state["recheck_pending"], r2.event), ([], "APPROVE"))

    def test_a_defect_past_a_cut_is_not_resolved(self):
        """Codex: a region line cut in the recheck item must not count as seen."""
        line = "int big = " + "1+" * 2995 + "1;"  # 6002 characters
        self.two_issues(line190=line)
        r = self.go([rule(resolve_rechecked=True)], *self.CAP)
        self.assertEqual(sorted(self.rechecked(r)), sorted([self.ids["L10"], self.ids["L190"]]))
        self.assertIn("190: " + line[:4000] + " …(줄 잘림)", r.prompts[0])
        self.assertEqual((r.issue("L10")["status"], r.issue("L190")["status"]),
                         ("resolved", "open"))
        self.assertEqual((r.state["recheck_pending"], r.event), ([], "REQUEST_CHANGES"))
        self.assertIn("줄이 너무 길어 재검토 불가: %s" % self.ids["L190"], self.stdout)
        posts = len(self.gh.posted)
        self.assertEqual(self.run_main("script", "--repo", self.R, "--pr", "1", *self.CAP), 0)
        self.assertIn("새로 리뷰할 변경 없음", self.stdout)
        self.assertEqual(len(self.gh.posted), posts)

    def test_a_region_line_under_4000_is_shown_whole(self):
        line = "int big = " + "1+" * 1495 + "1;"  # 3002 characters
        self.two_issues(line190=line)
        r = self.go([rule(resolve_rechecked=True)], *self.CAP)
        self.assertIn("190: " + line + "\n", r.prompts[0])
        self.assertEqual([i["status"] for i in r.state["issues"]], ["resolved", "resolved"])
        self.assertEqual((r.state["recheck_pending"], r.event), ([], "APPROVE"))
