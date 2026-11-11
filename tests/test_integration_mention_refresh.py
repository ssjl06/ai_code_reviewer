"""The PR read again before a mention mutates anything: closed or moved during the session,
a head the API does not show after the bot's own push, a push rebased on someone else's."""
import unittest
from unittest import mock

from review import rules_git
from review.mention import handle
from tests.mention_case import BLOCKER, MentionBase, answer, guideline
from tests.test_integration import git

EXTRA = dict(BLOCKER, file="src/u.cu", line=1, title="전역 변수 사용")
RULE_ASK = "@ai-bot /지침 기본 스트림 직후 cudaMemcpy는 지적하지 마"


class RefreshTest(MentionBase):
    def run_phases(self, *extra):
        return super().run_phases("--set", "mention_head_delay_s=0", *extra)

    def during_session(self, act):
        real = handle._session

        def session(*a, **kw):
            out = real(*a, **kw)
            act()
            return out
        return mock.patch.object(handle, "_session", session)

    def other_push(self, path="src/v.cu"):
        """A person pushes a commit to the PR branch (on the remote's tip)."""
        other = self.tmp + "/other"
        git(self.tmp, "clone", "-q", "-b", "feature", self.bare, other)
        with open(other + "/" + path.split("/")[-1], "w") as f:
            f.write("int v = 0;\n")
        git(other, "add", "-A")
        git(other, "-c", "user.name=o", "-c", "user.email=o@x", "commit", "-q", "-m", "other")
        git(other, "push", "-q", "origin", "HEAD:feature")

    def rule_mention(self):
        self.reviewed()
        m = self.mention(RULE_ASK)
        self.script(mention=[{"out": answer("남깁니다.", guideline=guideline(),
                                            new_issues=[EXTRA])}])
        return m, len(self.gh.reviews)

    def test_closed_during_session_replies_only(self):
        m, reviews = self.rule_mention()
        with self.during_session(lambda: setattr(self.gh, "state", "closed")):
            self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        self.assertIn("PR이 닫혀 답변만 함", self.stdout)
        self.assertEqual((self.trailers(m["key"]), len(self.gh.reviews)), ([], reviews))
        self.assertEqual(len(self.replies(m["key"])), 1)

    def test_head_moved_during_session_stops(self):
        m, reviews = self.rule_mention()
        with self.during_session(self.other_push):
            self.assertEqual(self.run_phases(), 1)
        self.assertIn("[m1] head가 바뀌어 언급 처리를 중단함", self.stdout)
        self.assertEqual((self.trailers(m["key"]), len(self.gh.reviews)), ([], reviews))
        self.assertEqual(self.replies(), [])

    def test_head_not_shown_after_own_push_posts_nothing(self):
        m, reviews = self.rule_mention()
        self.gh.lag_after_push(50)  # longer than the head wait
        self.assertEqual(self.run_phases(), 1)
        self.assertEqual(len(self.trailers(m["key"])), 1)
        self.assertEqual((len(self.gh.reviews), self.replies()), (reviews, []))
        self.assertIn("[m1] head가 바뀌어 언급 처리를 중단함", self.stdout)

    def test_push_rebased_on_someone_elses_commit_stops(self):
        m, reviews = self.rule_mention()
        real = rules_git.push_change

        def push_change(*a, **kw):
            self.other_push()  # lands between the check and the push: the retry rebases
            return real(*a, **kw)
        with mock.patch.object(rules_git, "push_change", push_change):
            self.assertEqual(self.run_phases(), 1)
        self.assertIn("지침 커밋이 다른 사람의 커밋 위에 올라가 언급 처리를 중단함", self.stdout)
        self.assertEqual(len(self.trailers(m["key"])), 1)
        self.assertEqual((len(self.gh.reviews), self.replies()), (reviews, []))


if __name__ == "__main__":
    unittest.main()
