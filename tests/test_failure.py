import contextlib
import io
from unittest import mock

from review.failure import failure_body
from review.render import MARKER
from tests.test_integration import IntegrationBase


class FailureTest(IntegrationBase):
    def run_boom(self, *args):
        err = io.StringIO()
        with mock.patch("review.cli.build_chunks", side_effect=RuntimeError("kaboom " * 60)), \
                contextlib.redirect_stderr(err):
            rc = self.run_main("ok", "--repo", self.R, *args)
        return rc, err.getvalue()

    def test_unexpected_exception_posts_failure_comment(self):
        rc, err = self.run_boom("--pr", "1")
        self.assertEqual(rc, 1)
        self.assertIn("Traceback", err)
        self.assertIn("RuntimeError: kaboom", err)
        posted = self.gh.posted[-1]
        self.assertEqual(posted["event"], "COMMENT")
        self.assertEqual(posted["commit_id"], self.C1)
        self.assertTrue(posted["body"].startswith(
            "## 🤖 AI Code Review — 💬 코멘트\n**판정 근거:** AI 리뷰 실패 (RuntimeError: kaboom"))
        self.assertNotIn(MARKER, posted["body"])

    def test_dry_run_exception_posts_nothing(self):
        rc, err = self.run_boom("--dry-run", "--base", self.BASE, "--head", self.C1)
        self.assertEqual(rc, 1)
        self.assertIn("kaboom", err)
        self.assertEqual(self.gh.requests, [])

    def test_failure_body_truncates_message(self):
        body = failure_body(ValueError("x" * 500))
        self.assertEqual(body, "## 🤖 AI Code Review — 💬 코멘트\n**판정 근거:** AI 리뷰 실패 "
                               "(ValueError: %s)" % ("x" * 200))
        self.assertIn("&lt;!--", failure_body(ValueError("<!-- s")))
