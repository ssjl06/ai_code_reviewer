"""In-process fake GitHub REST server for the integration test (127.0.0.1, random port)."""
import json
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.fakes.fake_comments import MAX_REVIEW_BODY, WEB, CommentsMixin

STATES = {"APPROVE": "APPROVED", "REQUEST_CHANGES": "CHANGES_REQUESTED", "COMMENT": "COMMENTED"}
PR = "/repos/o/r/pulls/1"
ISSUE_COMMENTS = "/repos/o/r/issues/1/comments"


class FakeGitHub(CommentsMixin):
    def __init__(self, base, head, title="PR 제목", login="ai-bot"):
        self.base, self._head, self.title, self.login = base, head, title, login
        self.reviews, self.posted, self.dismissed, self.requests = [], [], [], []
        self.events = []  # issue events; a dismissal adds a `review_dismissed` one
        self.issue_comments, self.review_comments = [], []
        self.permissions = {}  # login -> permission; others answer 404
        self.draft, self.state, self.merged = False, "open", False
        self.head_ref, self.full_name, self.clone_url = "feature", "o/r", ""
        self.head_remote = None  # a bare repository: the API head is its `head_ref` tip
        self.reject_inline = False  # answer 422 to a review that carries inline comments
        self.before_post = None  # called with the body just before a review is stored
        self.before_reply = None  # called with the body before a comment is stored; raising -> 403
        self.frozen_reviews = None  # when set, GET reviews answers this list (a lagging listing)
        self._fail = []  # [(status, path part)] for the next matching POSTs
        self._drop = []  # path parts: the next matching POST is stored but answers 502
        self._ids, self._clock = 100, 0
        self._lag, self._shown, self._pending, self._left = 0, None, None, 0  # lag_after_push
        self._lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n).decode("utf-8")) if n else None

            def _send(self, code, payload):
                data = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self):
                body = self._body()
                with fake._lock:
                    fake.requests.append({"method": self.command, "path": self.path,
                                          "body": body,
                                          "auth": self.headers.get("Authorization")})
                    path, _, query = self.path.partition("?")
                    code, payload = fake.dispatch(self.command, path, body)
                    if self.command == "GET" and code == 200 and isinstance(payload, list):
                        payload = fake.page(payload, query)
                self._send(code, payload)

            do_GET = do_POST = do_PUT = _handle

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def head(self):
        if self._head is None and self.head_remote:
            return subprocess.run(["git", "--git-dir", self.head_remote, "rev-parse",
                                   "refs/heads/" + self.head_ref], capture_output=True,
                                  text=True, check=True).stdout.strip()
        return self._head

    @head.setter
    def head(self, sha):
        self._head = sha

    def track_remote(self, bare):
        """The PR head repository is `bare`; the API head follows its branch tip."""
        self.head_remote, self.clone_url, self._head = bare, bare, None

    def lag_after_push(self, reads):
        """After the head moves, the PR keeps reporting the old head for `reads` reads, and a
        review on a commit it does not report yet is refused (422)."""
        self._lag = reads

    def _reported_head(self):
        tip = self.head
        if self._lag and self._shown is not None and tip != self._shown:
            if self._pending != tip:
                self._pending, self._left = tip, self._lag
            if self._left > 0:
                self._left -= 1
                return self._shown
        self._shown = tip
        return tip

    def fail_next_post(self, status, match=""):
        """The next POST whose path contains `match` answers `status` and stores nothing."""
        self._fail.append((status, match))

    def drop_response_once(self, match=""):
        """The next POST whose path contains `match` is stored but answers 502."""
        self._drop.append(match)

    def _pr(self):
        return {"number": 1, "title": self.title, "draft": self.draft, "state": self.state,
                "merged": self.merged, "base": {"sha": self.base, "ref": "main"},
                "head": {"sha": self._reported_head(), "ref": self.head_ref,
                         "repo": {"full_name": self.full_name, "clone_url": self.clone_url}}}

    def _take(self, items, path):
        for n, it in enumerate(items):
            if (it[1] if isinstance(it, tuple) else it) in path:
                return items.pop(n)
        return None

    def dispatch(self, method, path, body):
        if method == "POST":
            failed = self._take(self._fail, path)
            if failed:
                return failed[0], {"message": "injected failure"}
        code, payload = self.route(method, path, body)
        if method == "POST" and code == 200 and self._take(self._drop, path) is not None:
            return 502, {"message": "Bad Gateway"}
        return code, payload

    def _reply(self, body, extra):
        if self.before_reply:
            try:
                self.before_reply(body)
            except Exception as e:  # the hook refuses this comment
                return 403, {"message": "refused: %s" % e}
        c = dict(extra, id=self.next_id(), user={"login": self.login}, body=body["body"],
                 author_association="NONE", created_at=self.now())
        c["html_url"] = "%s#c%d" % (WEB, c["id"])
        (self.review_comments if "path" in c else self.issue_comments).append(c)
        return 200, c

    def route(self, method, path, body):
        if method == "GET" and path == PR:
            return 200, self._pr()
        if method == "GET" and path == PR + "/reviews":
            return 200, self.reviews if self.frozen_reviews is None else self.frozen_reviews
        if method == "GET" and path == "/repos/o/r/issues/1/events":
            return 200, self.events
        if method == "GET" and path == ISSUE_COMMENTS:
            return 200, self.issue_comments
        if method == "GET" and path == PR + "/comments":
            return 200, self.review_comments
        m = re.match(r"/repos/o/r/collaborators/([^/]+)/permission$", path)
        if method == "GET" and m:
            if m.group(1) in self.permissions:
                return 200, {"permission": self.permissions[m.group(1)]}
            return 404, {"message": "Not Found"}
        if method == "POST" and path == ISSUE_COMMENTS:
            return self._reply(body, {})
        m = re.match(re.escape(PR) + r"/comments/(\d+)/replies$", path)
        if method == "POST" and m:
            root = next((c for c in self.review_comments if c["id"] == int(m.group(1))), None)
            if root is None:
                return 404, {"message": "Not Found"}
            if root.get("in_reply_to_id"):  # GitHub answers only a thread's top comment
                return 422, {"message": "Unprocessable: replies to replies are not supported"}
            return self._reply(body, {"in_reply_to_id": root["id"], "path": root["path"],
                                      "line": root["line"], "side": root["side"]})
        if method == "POST" and path == PR + "/reviews":
            return self._post_review(body)
        m = re.match(re.escape(PR) + r"/reviews/(\d+)/dismissals$", path)
        if method == "PUT" and m:
            review = self.reviews[int(m.group(1)) - 1]
            review["state"] = "DISMISSED"
            self.dismissed.append(int(m.group(1)))
            self.add_dismissal(int(m.group(1)), self.login)
            return 200, review
        return 404, {"message": "Not Found"}

    def _post_review(self, body):
        if len(body.get("body") or "") > MAX_REVIEW_BODY:
            return 422, {"message": "Unprocessable: body is too long (maximum is 65536 characters)"}
        if self._lag and body["commit_id"] != self._shown:
            return 422, {"message": "Unprocessable: commit_id is not part of the pull request"}
        if self.reject_inline and body.get("comments"):
            return 422, {"message": "Unprocessable: line must be part of the diff"}
        if self.before_post:
            self.before_post(body)
        rid = len(self.reviews) + 1
        review = {"id": rid, "user": {"login": self.login},
                  "state": STATES[body["event"]], "body": body["body"],
                  "commit_id": body["commit_id"], "submitted_at": self.now(),
                  "html_url": "%s#pullrequestreview-%d" % (WEB, rid)}
        self.reviews.append(review)
        self.posted.append(body)
        for c in body.get("comments") or []:
            rc = dict(c, id=self.next_id(), user={"login": self.login}, in_reply_to_id=None,
                      author_association="NONE", created_at=review["submitted_at"],
                      pull_request_review_id=rid, start_line=None, diff_hunk="")
            rc["html_url"] = "%s#discussion_r%d" % (WEB, rc["id"])
            self.review_comments.append(rc)
        return 200, review

    def add_dismissal(self, review_id, login):
        """`login` dismisses review `review_id` (the PUT endpoint does it for the bot)."""
        self.reviews[review_id - 1]["state"] = "DISMISSED"
        self.events.append({"event": "review_dismissed", "actor": {"login": login},
                            "dismissed_review": {"review_id": review_id, "state": "dismissed"}})

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
