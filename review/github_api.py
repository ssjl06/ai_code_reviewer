"""Minimal GitHub (Enterprise Server) REST client for posting one PR review."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Optional

from review.render import parse_state

TIMEOUT_S = 30
PER_PAGE = 100


class GitHubError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__("GitHub API error %d: %s" % (status, message))
        self.status = status
        self.message = message


def _error_item(e: Any) -> str:
    if isinstance(e, str):
        return e
    if isinstance(e, dict) and e.get("message"):
        return str(e["message"])
    return json.dumps(e)


def _error_message(raw: bytes) -> str:
    """`message` plus GitHub's `errors` details (e.g. "Can not approve your own pull request")."""
    text = raw.decode("utf-8", "replace")
    try:
        data = json.loads(text)
        if isinstance(data, dict) and data.get("message"):
            errs = data.get("errors")
            items = [_error_item(e) for e in (errs if isinstance(errs, list) else [])]
            return str(data["message"]) + (": " + "; ".join(items) if items else "")
    except ValueError:
        pass
    return text[:500]


class GitHub:
    def __init__(self, api_url: str, token: str, repo: str, pr: int,
                 opener=urllib.request.urlopen):
        self.api_url = api_url.rstrip("/")
        self._token = token
        self.repo = repo
        self.pr = pr
        self._opener = opener

    def request(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        headers = {
            "Authorization": "Bearer " + self._token,
            "Accept": "application/vnd.github+json",
            "User-Agent": "ai-review",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.api_url + path, data=data,
                                     headers=headers, method=method)
        try:
            with self._opener(req, timeout=TIMEOUT_S) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            raise GitHubError(e.code, _error_message(e.read())) from None
        except (urllib.error.URLError, OSError) as e:
            raise GitHubError(0, "network error: %s" % type(e).__name__) from None
        return json.loads(raw.decode("utf-8")) if raw.strip() else None

    def _pr_path(self) -> str:
        return "/repos/%s/pulls/%d" % (self.repo, self.pr)

    def get_pr(self) -> dict:
        return self.request("GET", self._pr_path())

    def list_reviews(self) -> list:
        out: list = []
        page = 1
        while True:
            chunk = self.request(
                "GET", "%s/reviews?per_page=%d&page=%d" % (self._pr_path(), PER_PAGE, page))
            out.extend(chunk)
            if len(chunk) < PER_PAGE:
                return out
            page += 1

    def _bot_reviews(self, bot_login: str) -> list:
        want = bot_login.lower()
        mine = [r for r in self.list_reviews()
                if r.get("user") and str(r["user"].get("login", "")).lower() == want]
        return sorted(mine, key=lambda r: r.get("id", 0), reverse=True)

    def latest_state(self, bot_login: str) -> Optional[dict]:
        for r in self._bot_reviews(bot_login):
            state = parse_state(r.get("body"))
            if state is not None:
                return state
        return None

    def dismiss_change_requests(self, bot_login: str, message: str) -> int:
        count = 0
        for r in self._bot_reviews(bot_login):
            if r.get("state") == "CHANGES_REQUESTED":
                self.request("PUT", "%s/reviews/%s/dismissals" % (self._pr_path(), r["id"]),
                             {"message": message, "event": "DISMISS"})
                count += 1
        return count

    def post_review(self, commit_sha, event, body, comments: list) -> dict:
        return self.request("POST", self._pr_path() + "/reviews", {
            "commit_id": commit_sha, "event": event, "body": body, "comments": comments})

    def post_with_fallback(self, commit_sha, event, body, comments, fallback_body) -> dict:
        try:
            return self.post_review(commit_sha, event, body, comments)
        except GitHubError as e:
            if e.status != 422:
                raise
            if "own pull request" in e.message:
                event = "COMMENT"
            return self.post_review(commit_sha, event, fallback_body, [])
