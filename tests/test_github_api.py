import io
import json
import unittest
import urllib.error

from review.github_api import GitHub, GitHubError
from review.render import state_marker

MARKED = "hi\n" + state_marker({"sha": "abc", "findings": []})


class FakeResp:
    def __init__(self, status, data):
        self.status = status
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.timeouts = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        self.timeouts.append(timeout)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        status, payload = item
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "err", {}, io.BytesIO(data))
        return FakeResp(status, data)


def make(responses, url="https://ghe.example/api/v3/"):
    op = FakeOpener(responses)
    return GitHub(url, "T", "o/r", 7, opener=op), op


def rev(i, login="ai-bot", state="COMMENTED", body=None):
    user = None if login is None else {"login": login}
    return {"id": i, "user": user, "state": state, "body": body}


def C():
    return {"path": "a.py", "line": 3, "side": "RIGHT", "body": "x"}


class GitHubApiTest(unittest.TestCase):
    def test_auth_header_and_path(self):
        gh, op = make([(200, {"number": 7})])
        gh.get_pr()
        req = op.requests[0]
        self.assertEqual(req.get_header("Authorization"), "Bearer T")
        self.assertEqual(req.get_header("Accept"), "application/vnd.github+json")
        self.assertEqual(req.get_header("User-agent"), "ai-review")
        self.assertEqual(req.full_url, "https://ghe.example/api/v3/repos/o/r/pulls/7")
        self.assertEqual(op.timeouts[0], 30)

    def test_pagination(self):
        gh, op = make([(200, [rev(i) for i in range(100)]), (200, [rev(100)])])
        self.assertEqual(len(gh.list_reviews()), 101)
        self.assertIn("per_page=100&page=2", op.requests[1].full_url)

    def test_bot_login_case_insensitive(self):
        gh, _ = make([(200, [rev(1, login="AI-Bot", body=MARKED), rev(2, login="human", body=MARKED)])])
        self.assertEqual(gh.latest_state("ai-bot")["sha"], "abc")

    def test_latest_state_skips_corrupt_and_null_user(self):
        gh, _ = make([(200, [rev(1, body=MARKED), rev(2, body="<!-- junk"), rev(3, login=None, body=MARKED)])])
        self.assertEqual(gh.latest_state("ai-bot")["sha"], "abc")

    def test_latest_state_none(self):
        gh, _ = make([(200, [rev(1, body="plain")])])
        self.assertIsNone(gh.latest_state("ai-bot"))

    def test_dismiss_only_change_requests(self):
        gh, op = make([(200, [rev(1, state="CHANGES_REQUESTED"), rev(2, state="APPROVED"),
                              rev(3, login="human", state="CHANGES_REQUESTED")]), (200, {})])
        self.assertEqual(gh.dismiss_change_requests("ai-bot", "m"), 1)
        self.assertEqual(op.requests[1].get_method(), "PUT")
        self.assertTrue(op.requests[1].full_url.endswith("/pulls/7/reviews/1/dismissals"))
        self.assertEqual(json.loads(op.requests[1].data), {"message": "m", "event": "DISMISS"})

    def test_post_review_body(self):
        gh, op = make([(200, {"id": 1})])
        gh.post_review("sha", "COMMENT", "b", [])
        sent = json.loads(op.requests[0].data)
        self.assertEqual(sent, {"commit_id": "sha", "event": "COMMENT", "body": "b", "comments": []})
        self.assertEqual(op.requests[0].get_header("Content-type"), "application/json")

    def test_422_fallback_moves_comments(self):
        gh, op = make([(422, {"message": "line must be part of the diff"}), (200, {"id": 9})])
        gh.post_with_fallback("sha", "APPROVE", "b", [C()], "fb")
        sent = json.loads(op.requests[1].data)
        self.assertEqual((sent["body"], sent["comments"], sent["event"]), ("fb", [], "APPROVE"))

    def test_own_pr_approve_falls_back_to_comment(self):
        gh, op = make([(422, {"message": "Can not approve your own pull request"}), (200, {})])
        gh.post_with_fallback("sha", "APPROVE", "b", [C()], "fb")
        self.assertEqual(json.loads(op.requests[1].data)["event"], "COMMENT")

    def test_second_failure_raises(self):
        gh, _ = make([(422, {"message": "a"}), (422, {"message": "b"})])
        with self.assertRaises(GitHubError) as cm:
            gh.post_with_fallback("sha", "COMMENT", "b", [C()], "fb")
        self.assertEqual((cm.exception.status, cm.exception.message), (422, "b"))

    def test_non_422_not_retried(self):
        gh, op = make([(500, b"boom")])
        with self.assertRaises(GitHubError) as cm:
            gh.post_with_fallback("sha", "COMMENT", "b", [], "fb")
        self.assertEqual(cm.exception.message, "boom")
        self.assertEqual(len(op.requests), 1)

    def test_network_error_status_zero_no_token(self):
        gh, _ = make([urllib.error.URLError("down")])
        with self.assertRaises(GitHubError) as cm:
            gh.get_pr()
        self.assertEqual(cm.exception.status, 0)
        self.assertNotIn("T", cm.exception.message.replace("Timeout", ""))  # token is "T"
        gh, _ = make([TimeoutError("slow")])
        with self.assertRaises(GitHubError) as cm:
            gh.get_pr()
        self.assertEqual(cm.exception.status, 0)

    def test_long_error_truncated(self):
        gh, _ = make([(500, b"x" * 900)])
        with self.assertRaises(GitHubError) as cm:
            gh.get_pr()
        self.assertEqual(len(cm.exception.message), 500)


if __name__ == "__main__":
    unittest.main()
