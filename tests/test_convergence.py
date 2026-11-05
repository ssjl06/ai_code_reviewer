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

    def test_reworded_reraise_converges(self):
        """The model raises one issue in new words on a neighboring line on every push."""
        runs = [self.go([rule("- src/k.cu\n", [find("src/k.cu", 2)])])]
        for n, extra in enumerate(("int z2;\n", "int z2;\nint z3;\n"), 1):
            self.push({"src/k.cu": BUGGY + extra})
            line = 3 + n  # 4, then 5: within 3 lines of the region at line 2
            runs.append(self.go([rule("- src/k.cu\n", [find("src/k.cu", line, "Reworded %d" % n)])]))
        self.assertEqual([r.inline for r in runs], [1, 0, 0])
        self.assertEqual([r.new_findings for r in runs], [1, 0, 0])
        self.assertEqual(len(runs[-1].state["issues"]), 1)
        self.assertEqual(runs[-1].event, "REQUEST_CHANGES")  # still blocked by the one issue

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
        r = self.go([rule("- src/k.cu\n", [find("src/k.cu", 9, "Same defect, reworded")])])
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
        r1 = self.go([claim], "--set", "max_chunks=1", "--set", "chunk_max_tokens=30")
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
