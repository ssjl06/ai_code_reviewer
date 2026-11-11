"""Mention phase end to end: its inputs (the checked-out head, `mention_since` from the
environment, a review body that mentions the bot)."""
import os
import unittest
from unittest import mock

from review.render import parse_state
from tests.mention_case import MentionBase, answer
from tests.test_integration import FIXED, git


class MentionInputsTest(MentionBase):
    def test_review_phase_uses_the_checked_out_commit(self):
        self.commit_and_push({"src/k.cu": FIXED})  # the API head is this newer commit
        git(self.R, "checkout", "-q", "--detach", self.C1)
        self.script(review={"summary": "없음", "findings": [], "previous_findings": [],
                            "incomplete_reason": None, "diff_reviewed": True})
        self.run_phases()
        self.assertEqual(len(self.gh.posted), 1, self.stdout + self.stderr)
        self.assertEqual(self.gh.posted[0]["commit_id"], self.C1)
        self.assertEqual(parse_state(self.gh.posted[0]["body"])["sha"], self.C1)

    def test_mention_since_from_the_environment(self):
        self.gh.draft = True
        self.mention("@ai-bot 예전 질문", created_at="2026-11-10T09:00:00Z")
        new = self.mention("@ai-bot 새 질문", created_at="2026-11-11T09:00:00Z")
        with mock.patch.dict(os.environ, {"AI_REVIEW_MENTION_SINCE": "2026-11-11T00:00:00Z"}):
            self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        self.assertIn("mention_since 이전의 멘션이라 건너뜁니다.", self.stdout)
        self.assertEqual([c["id"] for c in self.replies()],
                         [c["id"] for c in self.replies(new["key"])])
        self.assertEqual(len(self.replies()), 1)

    def test_malformed_mention_since_ends_the_phase(self):
        self.gh.draft = True
        self.mention("@ai-bot 질문")
        with mock.patch.dict(os.environ, {"AI_REVIEW_MENTION_SINCE": "어제 오후"}):
            self.assertEqual(self.run_phases(), 1)
        self.assertEqual(self.stdout.count("mention_since 형식 오류: 어제 오후 — 기준 없이 진행하지 않음"),
                         1, self.stdout)
        self.assertEqual(self.replies(), [])
        self.assertEqual(self.mention_calls(), [])

    def test_review_body_mention(self):
        self.reviewed()
        rid = len(self.gh.reviews) + 100
        self.gh.reviews.append({"id": rid, "user": {"login": "dev"}, "state": "COMMENTED",
                                "body": "@ai-bot 전체적으로 이 구조 괜찮아?",
                                "author_association": "MEMBER", "submitted_at": self.gh.now(),
                                "html_url": "https://ghe.example/o/r/pull/1#review-%d" % rid})
        self.script(mention=[{"out": answer("괜찮습니다.")}])
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        self.assertIn("(review-body, @dev)", self.stdout)
        [reply] = self.replies("v:%d" % rid)
        self.assertNotIn("in_reply_to_id", reply)  # an issue comment, not a thread reply
        self.assertIn(reply, self.gh.issue_comments)
        first = reply["body"].split("\n", 1)[0]
        self.assertTrue(first.startswith("> "), first)
        self.assertIn("전체적으로 이 구조 괜찮아? ([원문](https://ghe.example/o/r/pull/1#review-%d))"
                      % rid, first)
        self.assertIn("괜찮습니다.", reply["body"])
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        self.assertIn("[main] 언급 0건 처리", self.stdout)
        self.assertEqual(len(self.replies("v:%d" % rid)), 1)
        self.assertEqual(len(self.mention_calls()), 1)


if __name__ == "__main__":
    unittest.main()
