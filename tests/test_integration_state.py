import contextlib
import io
import json
import os
import unittest

from review import cli

from review.render import parse_state, state_marker
from tests.test_integration import BUGGY, FIXED, IntegrationBase


class IntegrationStateTest(IntegrationBase):
    def test_dry_run_with_state_file_is_incremental(self):
        out_dir = os.path.join(self.tmp, "dry")
        self.assertEqual(self.run_main("blocking", "--repo", self.R, "--dry-run", "--base",
                                       self.BASE, "--head", self.C1, "--out-dir", out_dir), 0)
        with open(os.path.join(out_dir, "review.json"), encoding="utf-8") as f:
            state = parse_state(json.load(f)["body"])
        self.assertEqual((state["v"], state["complete"]), (3, True))
        sf = os.path.join(self.tmp, "state.json")
        with open(sf, "w", encoding="utf-8") as f:
            json.dump(state, f)
        C2 = self.add_commit({"src/k.cu": BUGGY + "int z;\n"})
        rc = self.run_main("resolved", "--repo", self.R, "--dry-run", "--base", self.BASE,
                           "--head", C2, "--state-file", sf, "--out-dir", out_dir)
        self.assertEqual(rc, 0)
        self.assertIn("증분 리뷰", self.fake_calls()[-1]["stdin"])
        self.assertNotIn("only_in_c1_marker", self.fake_calls()[-1]["stdin"])

    def test_llm_down_exit_1(self):
        self.assertEqual(self.run_main("crash", "--repo", self.R, "--pr", "1"), 1)
        posted = self.gh.posted[-1]
        self.assertEqual(posted["event"], "COMMENT")
        self.assertIn("AI 리뷰 실패", posted["body"])
        self.assertIn("### 미완료/실패 원인\n- c1: boom: crashed", posted["body"])
        st = parse_state(posted["body"])
        self.assertEqual((st["sha"], st["complete"], st["seen"]), (self.C1, False, {}))

    def test_note_alone_is_approved_and_seen(self):
        self.assertEqual(self.run_main("noted", "--repo", self.R, "--pr", "1"), 0)
        posted = self.gh.posted[-1]
        self.assertEqual(posted["event"], "APPROVE")
        self.assertIn("(참고: 탐색 예산 부족)", posted["body"])
        st = parse_state(posted["body"])
        self.assertTrue(st["complete"])
        self.assertTrue(st["seen"] or st["seen_files"])

    def test_diff_not_reviewed_stays_new_for_the_next_run(self):
        self.assertEqual(self.run_main("unreviewed", "--repo", self.R, "--pr", "1"), 0)
        posted = self.gh.posted[-1]
        self.assertEqual(posted["event"], "COMMENT")
        self.assertIn("리뷰 미완료 조각 있음", posted["body"])
        self.assertIn("- c1: 변경 줄을 읽고 판단하지 못함", posted["body"])
        st = parse_state(posted["body"])
        self.assertEqual((st["complete"], st["seen"], st["seen_files"]), (False, {}, {}))
        self.assertEqual(self.run_main("noted", "--repo", self.R, "--pr", "1"), 0)
        self.assertEqual(self.gh.posted[-1]["event"], "APPROVE")  # same head reviewed again

    def test_plain_text_json_without_diff_reviewed_is_not_seen(self):
        for scn in ("text_missing", "text_null", "text_false_str"):
            self.assertEqual(self.run_main(scn, "--repo", self.R, "--pr", "1"), 0, scn)
            posted = self.gh.posted[-1]
            self.assertEqual(posted["event"], "COMMENT", scn)
            self.assertIn("- c1: diff_reviewed 필드 누락 또는 불리언 아님", posted["body"])
            st = parse_state(posted["body"])
            self.assertEqual((st["complete"], st["seen"], st["seen_files"]), (False, {}, {}), scn)
        self.assertEqual(self.run_main("noted", "--repo", self.R, "--pr", "1"), 0)
        self.assertEqual(self.gh.posted[-1]["event"], "APPROVE")  # same head reviewed again

    def test_failed_followup_keeps_change_request(self):
        self.assertEqual(self.run_main("blocking", "--repo", self.R, "--pr", "1"), 0)
        self.gh.head = self.add_commit({"src/k.cu": FIXED})
        self.assertEqual(self.run_main("crash", "--repo", self.R, "--pr", "1"), 1)
        # was COMMENT: the fix rewrote the flagged line, so the anchor was lost and the issue
        # "unclear". Now the issue is located, a failed run cannot confirm it, and it stays open.
        self.assertEqual(self.gh.posted[-1]["event"], "REQUEST_CHANGES")
        self.assertEqual(self.gh.dismissed, [])
        self.assertEqual(self.gh.reviews[0]["state"], "CHANGES_REQUESTED")
        st = parse_state(self.gh.posted[-1]["body"])
        self.assertEqual((st["complete"], [i["status"] for i in st["issues"]]), (False, ["open"]))


    def test_fix_in_another_file_resolves_through_the_recheck_context(self):
        self.assertEqual(self.run_main("blocking", "--repo", self.R, "--pr", "1"), 0)
        fid = parse_state(self.gh.posted[-1]["body"])["issues"][0]["id"]
        self.gh.head = self.add_commit({"src/u.cu": "int other = 2;\n"})  # k.cu untouched
        self.assertEqual(self.run_main("resolved", "--repo", self.R, "--pr", "1"), 0)
        prompt = self.fake_calls()[-1]["stdin"]
        self.assertIn("다시 확인하기 위한 현재 코드", prompt)
        self.assertIn("src/k.cu", prompt)
        self.assertIn("2:   return p[1];", prompt)
        self.assertIn('same_as', prompt)
        posted = self.gh.posted[-1]
        self.assertEqual(posted["event"], "APPROVE")
        st = parse_state(posted["body"])
        self.assertEqual([(i["id"], i["status"]) for i in st["issues"]], [(fid, "resolved")])
        self.assertEqual(st["recheck_pending"], [])

    def dry_run_state(self):
        out_dir = os.path.join(self.tmp, "dry")
        self.assertEqual(self.run_main("blocking", "--repo", self.R, "--dry-run", "--base",
                                       self.BASE, "--head", self.C1, "--out-dir", out_dir), 0)
        with open(os.path.join(out_dir, "review.json"), encoding="utf-8") as f:
            return out_dir, parse_state(json.load(f)["body"])

    def test_unknown_previous_commit_is_logged_as_weak_tracking(self):
        out_dir, state = self.dry_run_state()
        sf = os.path.join(self.tmp, "state.json")
        with open(sf, "w", encoding="utf-8") as f:
            json.dump(dict(state, sha="0" * 40), f)
        C2 = self.add_commit({"src/k.cu": BUGGY + "int z;\n"})
        self.assertEqual(self.run_main("silent", "--repo", self.R, "--dry-run", "--base",
                                       self.BASE, "--head", C2, "--state-file", sf,
                                       "--out-dir", out_dir), 0)
        self.assertIn("[main] 이전 커밋을 찾을 수 없어 위치 추적이 약해짐", self.stdout)

    def test_known_previous_commit_is_not_logged_as_weak_tracking(self):
        out_dir, state = self.dry_run_state()
        sf = os.path.join(self.tmp, "state.json")
        with open(sf, "w", encoding="utf-8") as f:
            json.dump(state, f)
        C2 = self.add_commit({"src/k.cu": BUGGY + "int z;\n"})
        self.assertEqual(self.run_main("silent", "--repo", self.R, "--dry-run", "--base",
                                       self.BASE, "--head", C2, "--state-file", sf,
                                       "--out-dir", out_dir), 0)
        self.assertNotIn("위치 추적이 약해짐", self.stdout)
        self.assertIn("다시 확인하기 위한 현재 코드", self.fake_calls()[-1]["stdin"])

    def test_pushed_state_carries_recheck_pending_only_for_open_issues(self):
        review = {"previous": [{"id": "a", "status": "open"}, {"id": "b", "status": "resolved"},
                               {"id": "c", "status": "unclear"}]}
        self.assertEqual(cli._pending(review, ["a", "b", "c", "x"]), ["a", "c"])

    def test_each_duplicate_and_link_is_logged(self):
        review = {"duplicates": [{"file": "a.cc", "line": 7, "dup_of": "i1"}],
                  "links": [{"id": "i2", "file": "b.cc", "line": 9}]}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli._log_matches(review)
            cli._log_left_out(["o1", "o2"], ["r1"])
        text = out.getvalue()
        self.assertIn("[main] 기존 이슈와 중복되어 생략: a.cc:7 (이슈 i1)", text)
        self.assertIn("[main] 기존 이슈에 연결: b.cc:9 (이슈 i2)", text)
        self.assertIn("고아 이슈 2건은 프롬프트 목록에서 제외됨: o1, o2", text)
        self.assertIn("다시 확인할 이슈 1건을 이번 실행에 담지 못함", text)
        self.assertIn("r1", text)


class Outcome:
    """What a scripted run of the CLI left behind."""
    def __init__(self, test, **kw):
        self.model_calls = kw.pop("model_calls", 0)
        self.__dict__.update(kw)
        self.posted_event = test.gh.posted[-1]["event"]
        self.state = parse_state(test.gh.posted[-1]["body"])
        self.body = test.gh.posted[-1]["body"]

    def issue(self, id_):
        return next(i for i in self.state["issues"] if i["id"] == id_)


class WaiverFlowTest(IntegrationBase):
    def first_review(self, also_open=None):
        """Review 1 blocks on one issue; with `also_open` a second review (id 2) lists that
        issue too and one more open issue `also_open`."""
        self.assertEqual(self.run_main("blocking", "--repo", self.R, "--pr", "1"), 0)
        first = parse_state(self.gh.posted[-1]["body"])
        if also_open:
            more = dict(first["issues"][0], id=also_open)
            self.gh.reviews.append({"id": 2, "user": {"login": "ai-bot"},
                                    "state": "CHANGES_REQUESTED", "commit_id": self.C1,
                                    "body": "x\n" + state_marker(
                                        dict(first, issues=first["issues"] + [more]))})
        return first

    def calls(self):
        return len(self.fake_calls())

    def run_cli_same_sha(self, dismissed, also_open=None):
        prev = self.first_review(also_open)
        before = self.calls()
        for review_id in dismissed:
            self.gh.add_dismissal(review_id, "alice")
        posts = len(self.gh.posted)
        self.assertEqual(self.run_main("crash", "--repo", self.R, "--pr", "1"), 0)
        self.assertEqual(len(self.gh.posted), posts + 1)
        return Outcome(self, prev=prev, model_calls=self.calls() - before)

    def run_cli_twice(self):
        """The person dismisses review 1 while the run on the new head is posting its review."""
        prev = self.first_review()
        self.gh.head = self.add_commit({"docs/n.md": "x\n"})
        self.gh.before_post = lambda body: self.gh.add_dismissal(1, "alice")
        self.assertEqual(self.run_main("silent", "--repo", self.R, "--pr", "1"), 0)
        self.gh.before_post = None
        second_run = Outcome(self, prev=prev)
        self.assertEqual(second_run.posted_event, "REQUEST_CHANGES")  # it did not know yet
        before = self.calls()
        self.assertEqual(self.run_main("crash", "--repo", self.R, "--pr", "1"), 0)
        return Outcome(self, prev=prev, second=second_run, model_calls=self.calls() - before)

    def run_cli_with_push(self, dismissed):
        prev = self.first_review()
        for review_id in dismissed:
            self.gh.add_dismissal(review_id, "alice")
        self.gh.head = self.add_commit({"docs/n.md": "x\n"})
        before = self.calls()
        self.assertEqual(self.run_main("silent", "--repo", self.R, "--pr", "1"), 0)
        return Outcome(self, prev=prev, model_calls=self.calls() - before)

    def test_new_waiver_on_the_same_sha_settles_without_a_model(self):
        r = self.run_cli_same_sha(dismissed=[1])
        self.assertEqual(r.model_calls, 0)
        self.assertEqual(r.posted_event, "APPROVE")
        self.assertEqual(r.state["seen"], r.prev["seen"])
        self.assertEqual(r.state["seen_files"], r.prev["seen_files"])
        self.assertEqual((r.state["sha"], r.state["complete"]), (self.C1, True))
        self.assertEqual(r.issue(r.prev["issues"][0]["id"])["status"], "waived")
        self.assertIn("면제 1건", r.body)

    def test_settle_keeps_blocking_when_another_issue_is_open(self):
        r = self.run_cli_same_sha(dismissed=[1], also_open="i2")
        self.assertEqual(r.posted_event, "REQUEST_CHANGES")
        self.assertEqual(r.model_calls, 0)
        self.assertEqual(r.issue("i2")["status"], "open")
        self.assertEqual(r.issue(r.prev["issues"][0]["id"])["status"], "waived")

    def test_dismissal_made_during_a_run_applies_on_the_next_run(self):
        r = self.run_cli_twice()
        self.assertEqual(r.posted_event, "APPROVE")
        self.assertEqual(r.model_calls, 0)

    def test_a_waiver_is_applied_once(self):
        r = self.run_cli_same_sha(dismissed=[1])
        posts = len(self.gh.posted)
        self.assertEqual(self.run_main("crash", "--repo", self.R, "--pr", "1"), 0)
        self.assertEqual(len(self.gh.posted), posts)  # the bot's own dismissal is no waiver
        self.assertIn("새로 리뷰할 변경 없음", self.stdout)

    def test_a_waiver_with_a_push_of_only_excluded_files_still_posts(self):
        prev = self.first_review()
        self.gh.add_dismissal(1, "alice")
        self.gh.head = self.add_commit({"vendor/lib.c": "int v;\n"})
        before, posts = self.calls(), len(self.gh.posted)
        self.assertEqual(self.run_main("crash", "--repo", self.R, "--pr", "1",
                                       "--set", 'exclude_globs=["vendor/**"]'), 0)
        self.assertEqual((self.calls() - before, len(self.gh.posted)), (0, posts + 1))
        r = Outcome(self, prev=prev)
        self.assertEqual(r.posted_event, "APPROVE")
        self.assertEqual(r.issue(prev["issues"][0]["id"])["status"], "waived")
        self.assertEqual(self.run_main("crash", "--repo", self.R, "--pr", "1",
                                       "--set", 'exclude_globs=["vendor/**"]'), 0)
        self.assertEqual(len(self.gh.posted), posts + 1)
        self.assertIn("새로 리뷰할 변경 없음", self.stdout)

    def test_waived_issue_stays_waived_after_a_push(self):
        r = self.run_cli_with_push(dismissed=[1])
        self.assertEqual(r.issue(r.prev["issues"][0]["id"])["status"], "waived")
        self.assertEqual(r.posted_event, "APPROVE")
        self.assertIn("면제 1건", r.body)


if __name__ == "__main__":
    unittest.main()
