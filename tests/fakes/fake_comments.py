"""Comment helpers of the fake GitHub: ids, a clock, and comments made by people."""
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

MAX_REVIEW_BODY = 65536  # GitHub refuses a longer review body (422)

WEB = "https://ghe.example/o/r/pull/1"
T0 = datetime(2026, 11, 11, tzinfo=timezone.utc)


class CommentsMixin:
    """Needs `issue_comments`, `review_comments`, `login`, `_ids` and `_clock`."""

    def now(self, created_at=None):
        if created_at:
            return created_at
        self._clock += 1
        return (T0 + timedelta(seconds=self._clock)).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def page(items, query):
        """One page of a list route, as GitHub serves it: `per_page` (default 30, at most
        100) and `page` (from 1)."""
        q = parse_qs(query)
        per = min(int((q.get("per_page") or ["30"])[0]), 100)
        n = int((q.get("page") or ["1"])[0])
        return list(items)[(n - 1) * per:n * per]

    def next_id(self):
        self._ids += 1
        return self._ids

    def add_issue_comment(self, body, login="dev", assoc="MEMBER", created_at=None):
        c = {"id": self.next_id(), "user": {"login": login}, "body": body,
             "author_association": assoc, "created_at": self.now(created_at)}
        c["html_url"] = "%s#issuecomment-%d" % (WEB, c["id"])
        self.issue_comments.append(c)
        return c

    def add_review_comment(self, body, path="src/k.cu", line=2, login="dev", assoc="MEMBER",
                           in_reply_to=None, created_at=None, side="RIGHT"):
        c = {"id": self.next_id(), "user": {"login": login}, "body": body, "path": path,
             "line": line, "side": side, "start_line": None, "diff_hunk": "@@ -0,0 +1,3 @@",
             "author_association": assoc, "created_at": self.now(created_at),
             "in_reply_to_id": in_reply_to}
        if in_reply_to:
            root = next(r for r in self.review_comments if r["id"] == in_reply_to)
            c.update(path=root["path"], line=root["line"], side=root["side"])
        c["html_url"] = "%s#discussion_r%d" % (WEB, c["id"])
        self.review_comments.append(c)
        return c

    def bot_replies(self):
        """Every comment the bot posted (issue comments and thread replies)."""
        return [c for c in self.issue_comments + self.review_comments
                if c["user"]["login"] == self.login and not c.get("pull_request_review_id")]
