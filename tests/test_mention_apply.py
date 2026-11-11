import unittest

from review.mention.apply import plan_actions
from review.mention.ledger import apply_changes, ledger_event
from review.ledger import finding_id
from review.validate import normalize_mention

CFG = {"min_confidence": 50, "block_severities": ["critical", "major"], "block_confidence": 80}


def rec(n, status="open", blocking=False, file="a.py", line=10):
    return {"id": "%08x" % n, "file": file, "line": line, "lo": line, "hi": line,
            "status": status, "severity": "major" if blocking else "minor", "blocking": blocking,
            "anchor": "", "title": "제목 %d" % n, "excerpt": "", "orphan": False}


def state(issues=()):
    return {"v": 3, "sha": "h", "complete": True, "seen": {}, "seen_files": {}, "seen_hunks": {},
            "issues": list(issues), "recheck_pending": [], "approvable": True, "unclear": []}


def fnd(file="a.py", line=12, sev="major", conf=90, title="새 결함", **kw):
    return dict({"file": file, "line": line, "severity": sev, "confidence": conf,
                 "category": "bug", "title": title, "detail": "상세 설명"}, **kw)


def out(verdicts=(), new=(), guideline=None, wants=None, reply_="답"):
    return {"reply": reply_, "verdicts": list(verdicts), "new_issues": list(new),
            "guideline": guideline, "wants": wants}


def mention(command="withdraw"):
    return {"key": "r:1", "kind": "review-comment", "command": command, "author": "kim",
            "url": "https://x/1", "body": "b"}


def plan(o, m=None, targets=(), st="default", hunks=None, covered=None, same=None, anchor=None):
    if st == "default":
        st = state([rec(1), rec(2, blocking=False)])
    return plan_actions(o, m or mention(), list(targets), st,
                        {"a.py": {10, 11, 12, 13}} if hunks is None else hunks,
                        {"a.py", "b.py"} if covered is None else covered,
                        same or (lambda f: True), anchor, CFG)


class WithdrawTest(unittest.TestCase):
    def test_withdraw_needs_command_and_flag(self):
        t = [rec(1)]
        v = [{"id": rec(1)["id"], "withdraw": True, "reason": "r"}]
        self.assertEqual(plan(out(v), targets=t)["changes"]["w"], [rec(1)["id"]])
        self.assertEqual(plan(out([dict(v[0], withdraw=False)]), targets=t)["changes"]["w"], [])
        self.assertEqual(plan(out(v), mention("rule-add"), t)["changes"]["w"], [])

    def test_first_verdict_of_an_id_counts(self):
        i = rec(1)["id"]
        v = [{"id": i, "withdraw": False, "reason": ""}, {"id": i, "withdraw": True, "reason": ""}]
        self.assertEqual(plan(out(v), targets=[rec(1)])["changes"]["w"], [])

    def test_resolved_target_dropped_with_reason(self):
        t = [rec(1, "resolved")]
        p = plan(out([{"id": t[0]["id"], "withdraw": True, "reason": ""}]), targets=t)
        self.assertEqual((p["changes"]["w"], p["done"]), ([], []))
        self.assertEqual(p["dropped"], ["이슈 %s는 이미 해결되어 철회할 수 없음" % t[0]["id"]])

    def test_non_target_verdicts_ignored(self):
        v = [{"id": rec(2)["id"], "withdraw": True, "reason": ""}]
        self.assertEqual(plan(out(v), targets=[rec(1)])["changes"]["w"], [])

    def test_already_waived_is_done(self):
        t = [rec(1, "waived")]
        p = plan(out([{"id": t[0]["id"], "withdraw": True, "reason": ""}]), targets=t)
        self.assertEqual((p["changes"]["w"], p["done"]), ([], [t[0]["id"]]))

    def test_injected_withdrawal_without_command(self):
        v = [{"id": rec(1)["id"], "withdraw": True, "reason": "x"}]
        p = plan(out(v), mention(None), [rec(1)])
        self.assertEqual(p["changes"], {"w": [], "n": [], "up": []})
        self.assertIsNone(p["hint"])
        self.assertEqual(plan(out(v, wants="withdraw"), mention(None), [])["hint"], "withdraw")
        self.assertIsNone(plan(out(v, wants="withdraw"))["hint"])  # a command is given


class NewIssueTest(unittest.TestCase):
    def test_in_hunk_registered_with_fields(self):
        p = plan(out(new=[fnd()]), anchor=lambda f, n: "0a0b0c0d")
        (f,) = p["findings"]
        self.assertTrue(f["blocking"])
        self.assertEqual(f["anchor"], "0a0b0c0d")
        self.assertEqual(p["changes"]["n"][0]["id"], f["id"])
        self.assertTrue(p["changes"]["n"][0]["blocking"])

    def test_findings_keep_display_fields(self):
        p = plan(out(new=[fnd(end_line=13, suggestion="고쳐라")]))
        f = p["findings"][0]
        for k in ("category", "confidence", "detail", "suggestion", "end_line", "title", "id"):
            self.assertIn(k, f)

    def test_not_blocking_below_confidence(self):
        f = plan(out(new=[fnd(conf=60)]))["findings"][0]
        self.assertFalse(f["blocking"])
        self.assertTrue(f["needs_check"])
        self.assertFalse(plan(out(new=[fnd(sev="minor")]), st=state())["findings"][0]["blocking"])

    def test_outside_hunk_noted(self):
        p = plan(out(new=[fnd(line=99)]))
        self.assertEqual(p["changes"]["n"], [])
        self.assertIn("diff 밖이라 이슈로 등록하지 않음", p["notes"])

    def test_excluded_file_not_registered(self):
        p = plan(out(new=[fnd()]), covered={"b.py"})
        self.assertEqual(p["changes"]["n"], [])
        self.assertIn("리뷰 대상이 아닌 파일이라 이슈로 등록하지 않음", p["notes"])

    def test_no_state_not_registered(self):
        p = plan(out(new=[fnd()]), st=None)
        self.assertEqual(p["changes"]["n"], [])
        self.assertIn("아직 리뷰가 없어 이슈로 등록하지 않음", p["notes"])

    def test_skip_check_order(self):
        p = plan(out(new=[fnd(file="z.py", line=99)]), st=None)
        self.assertEqual(p["notes"], ["아직 리뷰가 없어 이슈로 등록하지 않음"])
        p = plan(out(new=[fnd(file="z.py", line=99)]))
        self.assertEqual(p["notes"], ["리뷰 대상이 아닌 파일이라 이슈로 등록하지 않음"])
        p = plan(out(new=[fnd(line=99)]), same=lambda f: False)
        self.assertEqual(p["notes"], ["diff 밖이라 이슈로 등록하지 않음"])

    def test_blocking_next_to_open_nonblocking_is_new(self):
        st = state([rec(1, line=11)])  # open minor 1 line away
        p = plan(out(new=[fnd(line=12, title="다른 결함")]), st=st)
        self.assertEqual(len(p["changes"]["n"]), 1)
        self.assertEqual(p["notes"], [])
        self.assertEqual(p["findings"][0]["near_issue"], rec(1)["id"])

    def test_blocking_needs_block_confidence(self):
        self.assertTrue(plan(out(new=[fnd(conf=80)]), st=state())["findings"][0]["blocking"])
        self.assertFalse(plan(out(new=[fnd(conf=79)]), st=state())["findings"][0]["blocking"])

    def test_state_not_at_head(self):
        same = {"a.py": True}
        p = plan(out(new=[fnd()]), same=lambda f: same[f])
        self.assertEqual(len(p["changes"]["n"]), 1)
        same["a.py"] = False
        p = plan(out(new=[fnd()]), same=lambda f: same[f])
        self.assertEqual(p["changes"]["n"], [])
        self.assertIn("이 파일은 아직 리뷰되지 않은 변경이 있어 이슈로 등록하지 않음", p["notes"])

    def test_low_confidence_dropped_silently(self):
        p = plan(out(new=[fnd(conf=10)]))
        self.assertEqual((p["changes"]["n"], p["notes"]), ([], []))

    def test_equal_findings_keep_first(self):
        p = plan(out(new=[fnd(detail="첫째"), fnd(detail="둘째")]))
        self.assertEqual([f["detail"] for f in p["findings"]], ["첫째"])

    def test_duplicate_of_ledger(self):
        st = state([rec(1, line=12)])  # minor, open: a nearby minor report folds into it
        p = plan(out(new=[fnd(sev="minor", conf=60)]), st=st)
        self.assertEqual(p["changes"], {"w": [], "n": [], "up": []})
        self.assertIn("이미 이슈 %s로 등록됨" % rec(1)["id"], p["notes"])

    def test_link_upgrades_blocking(self):
        st = state([rec(1, line=12)])
        st["issues"][0].update(title="새 결함", severity="major")
        p = plan(out(new=[fnd(conf=95)]), st=st)
        self.assertEqual(p["changes"]["up"], [rec(1)["id"]])
        self.assertEqual(p["changes"]["n"], [])
        p = plan(out(new=[fnd(conf=60)]), st=st)  # not blocking: no upgrade
        self.assertEqual(p["changes"]["up"], [])

    def test_unique_id_against_closed_records(self):
        f = fnd()
        taken = rec(0, "resolved")
        taken["id"] = finding_id(f)
        p = plan(out(new=[f]), st=state([taken]))
        self.assertNotEqual(p["findings"][0]["id"], taken["id"])


class WaivedReportTest(unittest.TestCase):
    """A literal re-report of a waived issue is not a new issue (a resolved one is)."""
    def run_through(self, st, raw, anchor=None):
        o = normalize_mention(out(new=[dict(raw, same_as="zzzzzzzz")]))
        p = plan(o, mention(None), st=st, anchor=anchor)
        new, effect = apply_changes(st, p["changes"])
        return p, new, effect

    def test_same_place_and_title(self):
        st = state([dict(rec(1, "waived", blocking=True, line=11), title="새 결함", hi=13)])
        p, new, effect = self.run_through(st, fnd(line=12))
        self.assertEqual((effect["n"], len(new["issues"])), ([], 1))
        self.assertIn("이미 철회된 이슈 %s와 같은 지적이라 등록하지 않음" % rec(1)["id"], p["notes"])
        self.assertEqual(ledger_event(new, True, False), ledger_event(st, True, False))

    def test_same_finding_id(self):
        f = dict(fnd(), anchor="ab12cd34")
        st = state([dict(rec(1, "waived", blocking=True, line=40), title=f["title"],
                         id=finding_id(f))])
        p, new, effect = self.run_through(st, fnd(), anchor=lambda path, line: "ab12cd34")
        self.assertEqual(effect["n"], [])
        self.assertIn("이미 철회된 이슈 %s와 같은 지적이라 등록하지 않음" % finding_id(f), p["notes"])

    def test_other_title_or_resolved_is_new(self):
        waived = dict(rec(1, "waived", blocking=True, line=12), title="다른 결함")
        self.assertEqual(len(self.run_through(state([waived]), fnd())[2]["n"]), 1)
        resolved = dict(rec(1, "resolved", blocking=True, line=12), title="새 결함")
        self.assertEqual(len(self.run_through(state([resolved]), fnd())[2]["n"]), 1)


class GuidelineTest(unittest.TestCase):
    G = {"op": "add", "path": "g.md", "applies": ["*"], "rule": "r", "reason": "x"}

    def test_needs_rule_command(self):
        self.assertIsNone(plan(out(guideline=self.G), mention(None))["guideline"])
        self.assertIsNone(plan(out(guideline=self.G), mention("withdraw"))["guideline"])
        self.assertEqual(plan(out(guideline=self.G), mention("rule-add"))["guideline"], self.G)

    def test_rule_command_without_guideline(self):
        p = plan(out(), mention("rule-add"))
        self.assertEqual(p["dropped"], ["지침 내용을 만들지 못함"])
        self.assertEqual(plan(out(), mention("withdraw"))["dropped"], [])

    def test_guideline_op_must_match_command(self):
        p = plan(out(guideline=dict(self.G, op="remove")), mention("rule-add"))
        self.assertIsNone(p["guideline"])
        self.assertIn("명령과 다른 지침 작업", p["dropped"])
        p = plan(out(guideline=dict(self.G, op="update", target="R-1")), mention("rule-update"))
        self.assertIsNotNone(p["guideline"])


if __name__ == "__main__":
    unittest.main()
