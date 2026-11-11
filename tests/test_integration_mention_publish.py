"""Mention phase end to end: a lagging API after the bot's own push and the review phase's
post, failed publication steps, and what is left when a run stops early."""
import json
import os
import unittest
from unittest import mock

from review import rules
from review.mention import guideline as guideline_step
from review.mention import handle, publish
from review.render import parse_state
from tests.mention_case import BLOCKER, BLOCKING_REVIEW, PATH, RID, MentionBase, answer, guideline
from tests.test_integration import git

EXTRA = dict(BLOCKER, file="src/u.cu", line=1, title="전역 변수 사용")
RULE_ASK = "@ai-bot /지침 기본 스트림 직후 cudaMemcpy는 지적하지 마"


class MentionPublishTest(MentionBase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(publish, "HEAD_DELAY_S", 0)
        p.start()
        self.addCleanup(p.stop)

    def test_head_lag_after_own_push_waits(self):
        self.reviewed()
        m1, m2 = self.mention(RULE_ASK), self.mention("@ai-bot 고마워")
        self.script(mention=[{"when": "고마워", "out": answer("천만에요.")},
                             {"out": answer("남깁니다.", guideline=guideline())}])
        self.gh.lag_after_push(2)
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        self.assertNotIn("head가 바뀌어", self.stdout)
        self.assertEqual(len(self.replies(m1["key"]) + self.replies(m2["key"])), 2)

    def test_review_on_pushed_commit_waits_for_head(self):
        self.reviewed()
        self.mention(RULE_ASK)
        self.script(mention=[{"out": answer("남깁니다.", guideline=guideline(),
                                            new_issues=[EXTRA])}])
        self.gh.lag_after_push(2)
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        pushed = git(self.R, "rev-parse", "HEAD")
        self.assertNotEqual(pushed, self.C1)
        self.assertEqual(self.gh.posted[-1]["commit_id"], pushed)

    def test_head_moved_by_someone_else_after_own_push_stops(self):
        self.reviewed()
        m1, m2 = self.mention(RULE_ASK), self.mention("@ai-bot 고마워")
        self.script(mention=[{"when": "고마워", "out": answer("천만에요.")},
                             {"out": answer("남깁니다.", guideline=guideline())}])

        def other_push(body):  # a person pushes while the bot answers the first mention
            sha = git(self.R, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "other")
            git(self.R, "push", "-q", self.bare, sha + ":refs/heads/feature")
        self.gh.before_reply = other_push
        self.assertEqual(self.run_phases(), 1)
        self.assertIn("[m2] head가 바뀌어 언급 처리를 중단함", self.stdout)
        self.assertEqual((len(self.replies(m1["key"])), len(self.replies(m2["key"]))), (1, 0))

    def test_review_phase_state_handed_over(self):
        self.mention("@ai-bot 1번 줄 전역 변수 괜찮아?")
        self.script(review=BLOCKING_REVIEW,
                    mention=[{"out": answer("문제입니다.", new_issues=[EXTRA])}])
        self.gh.frozen_reviews = []  # the listing never shows the review phase's review
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        first, last = self.gh.posted
        pushed = parse_state(first["body"])
        st = parse_state(last["body"])
        self.assertEqual(st["sha"], self.C1)
        self.assertEqual(st["seen"], pushed["seen"])
        self.assertEqual({i["file"] for i in st["issues"]}, {"src/k.cu", "src/u.cu"})
        self.assertIn(pushed["issues"][0]["id"], [i["id"] for i in st["issues"]])

    def test_time_short_with_trailer_leaves_pending(self):
        self.reviewed()
        m = self.mention(RULE_ASK)
        self.script(mention=[{"out": answer("남깁니다.", guideline=guideline())}])
        self.gh.before_reply = lambda body: (_ for _ in ()).throw(RuntimeError("cancelled"))
        self.assertEqual(self.run_phases(), 1)
        self.gh.before_reply = None
        self.assertEqual(self.run_phases("--set", "mention_deadline_s=10"), 1)
        self.assertIn("이전 실행의 지침 커밋이 있어 다음 실행에서 처리함", self.stdout)
        self.assertEqual(self.replies(), [])
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        [reply] = self.replies(m["key"])
        self.assertIn("지침 %s %s 추가" % (PATH, RID), reply["body"])

    def test_push_without_commit_not_reported(self):
        self.reviewed()
        m = self.mention(RULE_ASK)
        self.script(mention=[{"out": answer("남깁니다.", guideline=guideline(),
                                            new_issues=[EXTRA])}])
        landed = ({"status": "added", "path": PATH, "rule_id": RID, "reason": ""}, None)
        with mock.patch.object(guideline_step.rules_git, "push_change", return_value=landed):
            self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        self.assertNotIn("g", parse_state(self.gh.posted[-1]["body"])["m"])
        [reply] = self.replies(m["key"])
        self.assertIn("- 반영 안 됨: 지침 %s: 커밋이 만들어지지 않았습니다" % PATH, reply["body"])
        self.assertNotIn("지침 %s %s 추가" % (PATH, RID), reply["body"])

    def test_fork_asks_no_new_path(self):
        full = {"applies": ["**/*.cu"], "title": "가득", "rules": [
            {"id": rules.rule_id(PATH, "규칙 %d" % k), "text": "규칙 %d" % k, "reason": "이유",
             "source": "PR #0"} for k in range(32)]}
        self.commit_and_push({"%s/%s" % (rules.RULES_DIR, PATH): rules.render_doc(full)})
        self.reviewed()
        self.gh.full_name = "someone/r"
        self.mention(RULE_ASK)
        self.script(mention=[{"out": answer("남깁니다.", guideline=guideline())}])
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        self.assertEqual(len(self.mention_calls()), 1)  # no resume for a new path

    def test_thread_reply_refused_twice(self):
        self.reviewed()
        root = next(c for c in self.gh.review_comments if c["user"]["login"] == "ai-bot")
        r = self.gh.add_review_comment("@ai-bot 왜 문제야?", in_reply_to=root["id"])
        self.gh.fail_next_post(403, "/replies")
        self.gh.fail_next_post(403, "/issues/1/comments")
        self.assertEqual(self.run_phases(), 1)
        self.assertIn("[m1] 답글 게시 실패", self.stdout)
        self.assertEqual(self.replies("r:%d" % r["id"]), [])

    def test_failed_review_then_second_mention_on_old_state(self):
        fid = self.reviewed()["issues"][0]["id"]
        m1 = self.mention("@ai-bot /철회 의도된 동작")
        m2 = self.mention("@ai-bot 1번 줄 전역 변수 괜찮아?")
        self.script(mention=[{"when": "전역 변수 괜찮아", "out": answer("문제입니다.",
                                                                    new_issues=[EXTRA])},
                             {"out": answer("철회합니다."), "withdraw_all": True}])
        self.gh.fail_next_post(403, "/reviews")
        self.assertEqual(self.run_phases(), 1)
        self.assertEqual((len(self.replies(m1["key"])), len(self.replies(m2["key"]))), (0, 1))
        st = parse_state(self.gh.posted[-1]["body"])
        self.assertEqual(st["m"]["k"], m2["key"])
        status = {i["id"]: i["status"] for i in st["issues"]}
        self.assertEqual(status[fid], "open")  # the failed withdrawal is not in this state
        self.assertEqual(len(status), 2)

    def test_summary_written_when_the_phase_aborts(self):
        self.reviewed()
        self.mention("@ai-bot 질문")
        with mock.patch.object(handle, "handle", side_effect=RuntimeError("bug")):
            self.assertEqual(self.run_phases(), 1)
        with open(os.path.join(self.tmp, "ai-review", "mention", "mention.json")) as f:
            [row] = json.load(f)
        self.assertIsNone(row["outcome"])
        with open(self.summary, encoding="utf-8") as f:
            self.assertIn("| m1 | %s | general | - | 처리 안 함 |" % row["key"], f.read())


if __name__ == "__main__":
    unittest.main()
