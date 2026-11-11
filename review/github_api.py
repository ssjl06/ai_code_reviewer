"""Minimal GitHub (Enterprise Server) REST client for posting one PR review."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional, Set

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


def retry_post(send: Callable[[], dict], stored: Callable[[], Optional[dict]]) -> dict:
    """Send once. After a network failure or a 5xx the POST may still have landed, so look
    for the stored object first and only send again when there is none."""
    try:
        return send()
    except GitHubError as e:
        if e.status != 0 and e.status < 500:
            raise
    found = stored()
    return found if found is not None else send()


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

    def _paged(self, path: str) -> list:
        out: list = []
        page = 1
        while True:
            chunk = self.request("GET", "%s?per_page=%d&page=%d" % (path, PER_PAGE, page))
            out.extend(chunk)
            if len(chunk) < PER_PAGE:
                return out
            page += 1

    def list_reviews(self) -> list:
        return self._paged(self._pr_path() + "/reviews")

    def list_issue_comments(self) -> list:
        return self._paged("/repos/%s/issues/%d/comments" % (self.repo, self.pr))

    def list_review_comments(self) -> list:
        return self._paged(self._pr_path() + "/comments")

    def post_issue_comment(self, body: str) -> dict:
        return self.request("POST", "/repos/%s/issues/%d/comments" % (self.repo, self.pr),
                            {"body": body})

    def reply_review_comment(self, root_id: int, body: str) -> dict:
        return self.request("POST", "%s/comments/%d/replies" % (self._pr_path(), root_id),
                            {"body": body})

    def permission(self, login: str) -> Optional[str]:
        """Repository permission of `login` (admin, write, read, none), None on 404."""
        try:
            data = self.request("GET", "/repos/%s/collaborators/%s/permission"
                                % (self.repo, urllib.parse.quote(login, safe="")))
        except GitHubError as e:
            if e.status == 404:
                return None
            raise
        return (data or {}).get("permission")

    def bot_reviews(self, bot_login: str) -> list:
        want = bot_login.lower()
        mine = [r for r in self.list_reviews()
                if r.get("user") and str(r["user"].get("login", "")).lower() == want]
        return sorted(mine, key=lambda r: r.get("id", 0), reverse=True)

    _bot_reviews = bot_reviews

    def latest_state(self, bot_login: str) -> Optional[dict]:
        for r in self._bot_reviews(bot_login):
            state = parse_state(r.get("body"))
            if state is not None:
                return state
        return None

    def _events(self) -> list:
        return self._paged("/repos/%s/issues/%d/events" % (self.repo, self.pr))

    def dismissed_ids(self, bot_login: str) -> Set[str]:
        """Issue ids that are open in the marker of a bot review someone else dismissed. The
        bot's own dismissals (`failure.post`) are not waivers. A v3 marker lists its open
        `issues`; a v2 marker lists every finding. A review without a readable marker is skipped."""
        want = bot_login.lower()
        gone = set()
        for e in self._events():
            actor = e.get("actor") or {}
            review = e.get("dismissed_review") or {}
            if (e.get("event") == "review_dismissed" and review.get("review_id") is not None
                    and str(actor.get("login", "")).lower() != want):
                gone.add(str(review["review_id"]))
        if not gone:
            return set()
        out: Set[str] = set()
        for r in self._bot_reviews(bot_login):
            state = parse_state(r.get("body"))
            if str(r.get("id")) not in gone or state is None:
                continue
            if state.get("v") == 3:
                entries = [i for i in state.get("issues") or [] if isinstance(i, dict)
                           and i.get("status") == "open"]
            elif state.get("v") == 2:
                entries = [f for f in state.get("findings") or [] if isinstance(f, dict)]
            else:
                continue
            out.update(e["id"] for e in entries if isinstance(e.get("id"), str))
        return out

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
