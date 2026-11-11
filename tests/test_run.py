"""run.main: review phase, then mention phase (phases stubbed)."""
import contextlib
import io
import os
import unittest
from unittest import mock

from review import run

ENV = {"GITHUB_API_URL": "http://gh.invalid", "GITHUB_REPOSITORY": "o/r",
       "AI_REVIEW_BOT_PAT": "pat-secret", "AI_REVIEW_BOT_NAME": "ai-bot",
       "AI_REVIEW_LLM_BASE_URL": "http://llm.invalid", "AI_REVIEW_LLM_API_KEY": "k"}
ARGV = ["--repo", ".", "--pr", "1"]


class FakeGH:
    def __init__(self, pr):
        self.pr = pr

    def get_pr(self):
        return self.pr


class RunTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, ENV)
        p.start()
        self.addCleanup(p.stop)
        self.calls = []

    def go(self, pr=None, review_rc=0, mention=None, argv=ARGV):
        pr = pr or {"draft": False, "state": "open", "merged": False}

        def review(a):
            self.calls.append("review")
            return review_rc

        def mention_run(args, env, cfg, gh):
            self.calls.append("mention")
            self.assertEqual(args.pr, 1)
            self.assertEqual(env["AI_REVIEW_BOT_NAME"], "ai-bot")
            self.assertIn("mention_deadline_s", cfg)
            self.assertIs(gh.pr, pr)
            return mention(args) if mention else 0

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(run.cli, "main", side_effect=review), \
                mock.patch.object(run.mention_cli, "run", side_effect=mention_run), \
                mock.patch.object(run, "GitHub", return_value=FakeGH(pr)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = run.main(list(argv))
        self.out, self.err = out.getvalue(), err.getvalue()
        return rc

    def test_review_then_mention(self):
        self.assertEqual(self.go(), 0)
        self.assertEqual(self.calls, ["review", "mention"])

    def test_draft_skips_review_phase(self):
        self.assertEqual(self.go({"draft": True, "state": "open", "merged": False}), 0)
        self.assertEqual(self.calls, ["mention"])
        self.assertIn("[main] 리뷰 단계 건너뜀: 초안", self.out)

    def test_closed_and_merged_skip_review_phase(self):
        for pr in ({"draft": False, "state": "closed", "merged": False},
                   {"draft": False, "state": "closed", "merged": True}):
            self.calls = []
            self.assertEqual(self.go(pr), 0)
            self.assertEqual(self.calls, ["mention"])
            self.assertIn("[main] 리뷰 단계 건너뜀: 닫힘", self.out)

    def test_exit_code_is_the_higher(self):
        self.assertEqual(self.go(review_rc=1, mention=lambda a: 0), 1)
        self.assertEqual(self.go(review_rc=0, mention=lambda a: 1), 1)
        self.assertEqual(self.go(review_rc=2, mention=lambda a: 1), 2)

    def test_mention_exception_gives_1_after_review(self):
        def boom(args):
            raise RuntimeError("bug in mention phase")
        self.assertEqual(self.go(mention=boom), 1)
        self.assertEqual(self.calls, ["review", "mention"])
        self.assertIn("RuntimeError: bug in mention phase", self.err)
        self.assertIn("Traceback", self.err)

    def test_keyboard_interrupt_propagates(self):
        def stop(args):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.go(mention=stop)
        self.calls = []

        def leave(args):
            raise SystemExit(143)
        with self.assertRaises(SystemExit):
            self.go(mention=leave)

    def test_dry_run_only_review(self):
        self.assertEqual(self.go(argv=["--dry-run", "--base", "a", "--head", "b"]), 0)
        self.assertEqual(self.calls, ["review"])

    def test_pr_read_failure_is_1(self):
        from review.github_api import GitHubError

        class Broken:
            def get_pr(self):
                raise GitHubError(0, "network error")
        out = io.StringIO()
        with mock.patch.object(run.cli, "main") as review, \
                mock.patch.object(run.mention_cli, "run") as mention, \
                mock.patch.object(run, "GitHub", return_value=Broken()), \
                contextlib.redirect_stdout(out):
            self.assertEqual(run.main(list(ARGV)), 1)
        review.assert_not_called()
        mention.assert_not_called()
        self.assertIn("GitHub 조회 실패", out.getvalue())

    def test_missing_env_left_to_review_phase(self):
        os.environ.pop("AI_REVIEW_BOT_PAT")
        with mock.patch.object(run.cli, "main", return_value=2) as review, \
                mock.patch.object(run.mention_cli, "run") as mention:
            self.assertEqual(run.main(list(ARGV)), 2)
        review.assert_called_once_with(ARGV)
        mention.assert_not_called()


if __name__ == "__main__":
    unittest.main()
