"""The fake GitHub keeps the REST limits the kit relies on (pages, replies to a thread root,
the review body size), and the mention phase works within them."""
import unittest

from review.github_api import GitHub, GitHubError
from tests.mention_case import MentionBase, answer


class FakeLimitsTest(MentionBase):
    def api(self):
        return GitHub(self.gh.url, "pat-secret", "o/r", 1)

    def test_list_routes_are_paged(self):
        for k in range(130):
            self.gh.add_issue_comment("댓글 %d" % k)
        page = self.api().request("GET", "/repos/o/r/issues/1/comments?per_page=100&page=2")
        self.assertEqual([c["body"] for c in page], ["댓글 %d" % k for k in range(100, 130)])
        self.assertEqual(len(self.api().request("GET", "/repos/o/r/issues/1/comments")), 30)
        self.assertEqual(len(self.api().list_issue_comments()), 130)

    def test_reply_to_a_reply_is_refused(self):
        root = self.gh.add_review_comment("루트")
        child = self.gh.add_review_comment("답글", in_reply_to=root["id"])
        with self.assertRaises(GitHubError) as e:
            self.api().reply_review_comment(child["id"], "x")
        self.assertEqual(e.exception.status, 422)
        self.assertEqual(self.api().reply_review_comment(root["id"], "x")["in_reply_to_id"],
                         root["id"])

    def test_review_body_limit(self):
        with self.assertRaises(GitHubError) as e:
            self.api().post_review(self.C1, "COMMENT", "x" * 65537, [])
        self.assertEqual(e.exception.status, 422)
        self.api().post_review(self.C1, "COMMENT", "x" * 65536, [])


class MentionPagingTest(MentionBase):
    def test_mention_among_many_comments_answered_once(self):
        self.reviewed()
        for k in range(60):
            self.gh.add_issue_comment("잡담 %d" % k)
        m = self.mention("@ai-bot 이 PR 요약해 줘")
        for k in range(60):
            self.gh.add_issue_comment("잡담 %d" % (60 + k))
        self.script(mention=[{"out": answer("요약입니다.")}])
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        self.assertIn("언급 1건 처리", self.stdout)
        self.assertEqual(len(self.replies(m["key"])), 1)
        self.assertEqual(len(self.mention_calls()), 1)

    def test_mention_in_a_reply_to_a_reply_answers_at_the_root(self):
        self.reviewed()
        root = next(c for c in self.gh.review_comments if c["user"]["login"] == "ai-bot")
        mid = self.gh.add_review_comment("글쎄요", in_reply_to=root["id"])
        r = self.gh.add_review_comment("@ai-bot 왜 문제야?", in_reply_to=mid["id"])
        self.script(mention=[{"out": answer("범위를 벗어납니다.")}])
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        [reply] = self.replies("r:%d" % r["id"])
        self.assertEqual(reply.get("in_reply_to_id"), root["id"])


if __name__ == "__main__":
    unittest.main()
