"""Mention phase recovery end to end: a review POST with an unknown outcome, a failed review
phase post, a failed dismissal after an approval, a model failure after a guideline push."""
import unittest

from review.render import parse_state
from tests.mention_case import (BLOCKER, BLOCKING_REVIEW, PATH, RID, MentionBase, answer,
                                guideline)

EXTRA = dict(BLOCKER, file="src/u.cu", line=1, title="전역 변수 사용")
UNKNOWN = "리뷰 게시 결과를 확인할 수 없어 언급 처리를 중단함"


class UnknownPostTest(MentionBase):
    def test_unknown_mention_review_stops_the_phase(self):
        first = self.reviewed()["issues"][0]["id"]
        a = self.mention("@ai-bot 1번 줄 전역 변수 괜찮아?")
        b = self.mention("@ai-bot /철회 %s 의도된 동작입니다" % first)
        self.script(mention=[{"when": "의도된 동작입니다", "out": answer("철회합니다."),
                              "withdraw_all": True},
                             {"when": "전역 변수 괜찮아", "out": answer("문제입니다.",
                                                                     new_issues=[EXTRA])}])
        self.gh.drop_response_once("/reviews")  # stored, answered 502
        self.gh.before_post = lambda body: self.gh.fail_next("GET", 503, "/reviews")
        posts = len(self.gh.reviews)
        self.assertEqual(self.run_phases(), 1)
        self.gh.before_post = None
        self.assertIn("[m1] " + UNKNOWN, self.stdout)
        self.assertEqual(len(self.mention_calls()), 1)  # B was not started
        self.assertEqual(len(self.gh.reviews), posts + 1)
        self.assertEqual(self.replies(), [])
        blocker = parse_state(self.gh.reviews[-1]["body"])["m"]["n"][0]
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        self.assertIn("이전 실행에서 반영했습니다.", self.replies(a["key"])[0]["body"])
        self.assertEqual(len(self.replies(b["key"])), 1)
        last = self.gh.posted[-1]
        st = parse_state(last["body"])
        self.assertEqual(st["m"]["k"], b["key"])
        self.assertEqual(st["m"]["w"], [first])
        self.assertIn(blocker, [i["id"] for i in st["issues"] if i["status"] == "open"])
        self.assertEqual(last["event"], "REQUEST_CHANGES")  # not APPROVE on a lost blocker

    def test_failed_review_phase_post_skips_mentions(self):
        self.mention("@ai-bot 1번 줄 전역 변수 괜찮아?")
        self.script(review=BLOCKING_REVIEW, mention=[{"out": answer("문제입니다.")}])
        self.gh.drop_response_once("/reviews")
        self.assertEqual(self.run_phases(), 1)
        self.assertIn("리뷰 게시 실패로 언급 단계를 건너뜀", self.stdout)
        self.assertEqual((self.mention_calls(), self.replies()), ([], []))
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        self.assertEqual(len(self.replies()), 1)


class DismissalTest(MentionBase):
    def approve_with_failed_dismissal(self):
        fid = self.reviewed()["issues"][0]["id"]
        m = self.mention("@ai-bot /철회 %s 의도된 동작" % fid)
        self.script(mention=[{"out": answer("철회합니다."), "withdraw_all": True}])
        self.gh.fail_next("PUT", 500, "/dismissals")
        self.assertEqual(self.run_phases(), 1)
        self.assertEqual(self.gh.posted[-1]["event"], "APPROVE")
        self.assertEqual((self.gh.dismissed, self.replies()), ([], []))
        return m

    def test_next_run_dismisses_then_replies(self):
        m = self.approve_with_failed_dismissal()
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        self.assertEqual(self.gh.dismissed, [1])
        self.assertEqual(len(self.replies(m["key"])), 1)

    def test_later_review_means_no_dismissal(self):
        m = self.approve_with_failed_dismissal()
        self.commit_and_push({"src/v.cu": "int v = 0;\n"})
        other = dict(BLOCKER, file="src/v.cu", line=1, title="새 차단 이슈")
        self.script(review=dict(BLOCKING_REVIEW, findings=[other]))
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        self.assertEqual(self.gh.posted[-1]["event"], "REQUEST_CHANGES")
        self.assertEqual(self.gh.dismissed, [])
        self.assertEqual(len(self.replies(m["key"])), 1)


class GuidelineThenModelFailureTest(MentionBase):
    def test_model_failure_reply_names_the_committed_guideline(self):
        self.reviewed()
        m = self.mention("@ai-bot /지침 기본 스트림 직후 cudaMemcpy는 지적하지 마")
        self.script(mention=[{"out": answer("남깁니다.", guideline=guideline())}])
        self.refuse_first_reply()
        self.assertEqual(self.run_phases(), 1)
        self.assertEqual(len(self.trailers(m["key"])), 1)
        self.script(mention=[{"out": None}])
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        [reply] = self.replies(m["key"])
        self.assertIn("처리하지 못했습니다", reply["body"])
        self.assertIn("지침 %s %s 추가" % (PATH, RID), reply["body"])
        self.assertEqual(len(self.trailers(m["key"])), 1)


if __name__ == "__main__":
    unittest.main()
