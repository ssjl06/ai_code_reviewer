import io
import json
import unittest
import urllib.error

from review.github_api import GitHub, GitHubError, retry_post
from review.render import state_marker

OPEN_I1 = "hi\n" + state_marker({"v": 3, "sha": "s", "issues": [{"id": "i1", "status": "open"}]})
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


def dismissal(login, review_id=1, state="CHANGES_REQUESTED"):
    return {"event": "review_dismissed", "actor": {"login": login},
            "dismissed_review": {"review_id": review_id, "state": state}}


def gh_with(dismissed_by, events_pages=1, body=None, state=None):
    """A client whose events list one dismissal of bot review 1 (v3 marker with open i1,
    resolved r1)."""
    marker = state if state is not None else {
        "v": 3, "sha": "s", "issues": [{"id": "i1", "status": "open"},
                                       {"id": "r1", "status": "resolved"}]}
    text = body if body is not None else "hi\n" + state_marker(marker)
    events = [{"event": "labeled"}] * (100 * (events_pages - 1)) + [dismissal(dismissed_by)]
    pages = [events[i:i + 100] for i in range(0, len(events), 100)]
    responses = [(200, pg) for pg in pages] + [(200, [rev(1, login="bot", state="DISMISSED", body=text)])]
    return make(responses)[0]


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

    def test_dismissal_by_a_person_waives_the_open_ids(self):
        self.assertEqual(gh_with(dismissed_by="alice").dismissed_ids("bot"), {"i1"})

    def test_dismissal_by_the_bot_is_not_a_waiver(self):
        self.assertEqual(gh_with(dismissed_by="BOT").dismissed_ids("bot"), set())

    def test_events_are_paginated(self):
        self.assertEqual(gh_with(dismissed_by="alice", events_pages=2).dismissed_ids("bot"), {"i1"})

    def test_a_marker_without_state_is_skipped(self):
        self.assertEqual(gh_with(dismissed_by="alice", body="no marker").dismissed_ids("bot"), set())

    def test_a_v2_marker_lists_every_finding_as_open(self):
        v2 = {"v": 2, "sha": "s", "findings": [{"id": "a"}, {"id": "b"}]}
        self.assertEqual(gh_with(dismissed_by="alice", state=v2).dismissed_ids("bot"), {"a", "b"})

    def test_a_dismissal_of_someone_elses_review_is_ignored(self):
        gh, _ = make([(200, [dismissal("alice", review_id=2)]),
                      (200, [rev(1, login="bot", body=OPEN_I1), rev(2, login="human", body=OPEN_I1)])])
        self.assertEqual(gh.dismissed_ids("bot"), set())

    def test_events_path_and_page_size(self):
        gh = gh_with(dismissed_by="alice")
        gh.dismissed_ids("bot")
        url = gh._opener.requests[0].full_url
        self.assertTrue(url.endswith("/repos/o/r/issues/7/events?per_page=100&page=1"), url)

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

    def test_own_pr_422_errors_array_falls_back_to_comment(self):
        body = {"message": "Unprocessable Entity",
                "errors": ["Can not approve your own pull request"]}
        gh, op = make([(422, body), (200, {})])
        gh.post_with_fallback("sha", "APPROVE", "b", [C()], "fb")
        self.assertEqual(json.loads(op.requests[1].data)["event"], "COMMENT")
        gh, op = make([(422, {"message": "Validation Failed",
                              "errors": [{"message": "x own pull request"}, {"code": "c"}]})])
        with self.assertRaises(GitHubError) as cm:
            gh.request("POST", "/p", {})
        self.assertEqual(cm.exception.message,
                         'Validation Failed: x own pull request; {"code": "c"}')

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


class CommentApiTest(unittest.TestCase):
    def test_lists_page_until_a_short_page(self):
        gh, op = make([(200, [{"id": i} for i in range(100)]), (200, [{"id": 100}])])
        self.assertEqual(len(gh.list_issue_comments()), 101)
        self.assertTrue(op.requests[0].full_url.endswith(
            "/repos/o/r/issues/7/comments?per_page=100&page=1"))
        self.assertTrue(op.requests[1].full_url.endswith("page=2"))

    def test_review_comments_path(self):
        gh, op = make([(200, [])])
        self.assertEqual(gh.list_review_comments(), [])
        self.assertTrue(op.requests[0].full_url.endswith(
            "/repos/o/r/pulls/7/comments?per_page=100&page=1"))

    def test_post_issue_comment(self):
        gh, op = make([(201, {"id": 5})])
        self.assertEqual(gh.post_issue_comment("hi"), {"id": 5})
        req = op.requests[0]
        self.assertEqual((req.method, req.full_url), (
            "POST", "https://ghe.example/api/v3/repos/o/r/issues/7/comments"))
        self.assertEqual(json.loads(req.data), {"body": "hi"})

    def test_reply_review_comment(self):
        gh, op = make([(201, {"id": 6})])
        gh.reply_review_comment(42, "yo")
        req = op.requests[0]
        self.assertEqual((req.method, req.full_url), (
            "POST", "https://ghe.example/api/v3/repos/o/r/pulls/7/comments/42/replies"))
        self.assertEqual(json.loads(req.data), {"body": "yo"})

    def test_bot_reviews_newest_first(self):
        gh, _ = make([(200, [rev(1), rev(3), rev(2, login="x")])])
        self.assertEqual([r["id"] for r in gh.bot_reviews("AI-Bot")], [3, 1])

    def test_permission(self):
        gh, op = make([(200, {"permission": "write"}), (404, {"message": "Not Found"})])
        self.assertEqual(gh.permission("alice"), "write")
        self.assertTrue(op.requests[0].full_url.endswith(
            "/repos/o/r/collaborators/alice/permission"))
        self.assertIsNone(gh.permission("ghost"))


class RetryPostTest(unittest.TestCase):
    def sender(self, results):
        calls = []

        def send():
            calls.append(1)
            r = results.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        return send, calls

    def test_returns_the_stored_object_after_a_502_without_a_second_send(self):
        send, calls = self.sender([GitHubError(502, "bad")])
        self.assertEqual(retry_post(send, lambda: {"id": 9}), {"id": 9})
        self.assertEqual(len(calls), 1)

    def test_sends_twice_when_nothing_is_stored(self):
        send, calls = self.sender([GitHubError(0, "net"), {"id": 1}])
        self.assertEqual(retry_post(send, lambda: None), {"id": 1})
        self.assertEqual(len(calls), 2)

    def test_a_422_raises(self):
        send, calls = self.sender([GitHubError(422, "bad")])
        with self.assertRaises(GitHubError):
            retry_post(send, lambda: {"id": 9})
        self.assertEqual(len(calls), 1)

    def test_success_does_not_look_up(self):
        send, _ = self.sender([{"id": 2}])
        self.assertEqual(retry_post(send, lambda: self.fail("looked up")), {"id": 2})
