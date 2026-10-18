"""In-process fake GitHub REST server for the integration test (127.0.0.1, random port)."""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATES = {"APPROVE": "APPROVED", "REQUEST_CHANGES": "CHANGES_REQUESTED", "COMMENT": "COMMENTED"}
PR = "/repos/o/r/pulls/1"


class FakeGitHub:
    def __init__(self, base, head, title="PR 제목", login="ai-bot"):
        self.base, self.head, self.title, self.login = base, head, title, login
        self.reviews, self.posted, self.dismissed, self.requests = [], [], [], []
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
                    code, payload = fake.route(self.command, self.path.split("?")[0], body)
                self._send(code, payload)

            do_GET = do_POST = do_PUT = _handle

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def route(self, method, path, body):
        if method == "GET" and path == PR:
            return 200, {"number": 1, "title": self.title,
                         "base": {"sha": self.base}, "head": {"sha": self.head}}
        if method == "GET" and path == PR + "/reviews":
            return 200, self.reviews
        if method == "POST" and path == PR + "/reviews":
            review = {"id": len(self.reviews) + 1, "user": {"login": self.login},
                      "state": STATES[body["event"]], "body": body["body"],
                      "commit_id": body["commit_id"]}
            self.reviews.append(review)
            self.posted.append(body)
            return 200, review
        m = re.match(re.escape(PR) + r"/reviews/(\d+)/dismissals$", path)
        if method == "PUT" and m:
            review = self.reviews[int(m.group(1)) - 1]
            review["state"] = "DISMISSED"
            self.dismissed.append(int(m.group(1)))
            return 200, review
        return 404, {"message": "Not Found"}

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
