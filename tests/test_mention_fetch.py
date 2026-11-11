import unittest

from review.mention import fetch
from review.render import reply_marker

BOT = "ai-bot"


def ic(i, body, user="alice", assoc="MEMBER", at="2026-11-11T07:00:00Z"):
    return {"id": i, "body": body, "user": {"login": user}, "author_association": assoc,
            "created_at": at, "html_url": "u%d" % i}


def rc(i, body, reply=None, at="2026-11-11T07:00:00Z", user="alice", **kw):
    d = ic(i, body, user=user, at=at)
    d.update({"path": "a.py", "side": "RIGHT", "line": 3, "start_line": None,
              "diff_hunk": "@@", "pull_request_review_id": 1})
    if reply:
        d["in_reply_to_id"] = reply
    d.update(kw)
    return d


def rv(i, body, at="2026-11-11T07:00:00Z", user="alice"):
    return {"id": i, "body": body, "user": {"login": user}, "author_association": "OWNER",
            "submitted_at": at, "html_url": "v", "state": "COMMENTED"}


def run(ics=(), rcs=(), rvs=(), since="", perm=lambda u: None):
    return fetch.pending(list(ics), list(rcs), list(rvs), BOT, since, perm)[0]


class PendingTest(unittest.TestCase):
    def test_kinds_and_fields(self):
        m = run([ic(1, "@ai-bot hi")], [rc(2, "@ai-bot a"), rc(3, "@ai-bot b", reply=2)],
                [rv(4, "@ai-bot c")])
        self.assertEqual([(x["key"], x["kind"]) for x in m],
                         [("i:1", "general"), ("r:2", "inline-new"),
                          ("r:3", "inline-reply"), ("v:4", "review-body")])
        self.assertEqual(m[1]["root_id"], 2)
        self.assertEqual(m[2]["root_id"], 2)
        self.assertEqual(m[2]["path"], "a.py")
        self.assertNotIn("path", m[0])
        self.assertEqual(m[0]["command"], None)

    def test_root_follows_chain(self):
        m = run(rcs=[rc(2, "x"), rc(3, "x", reply=2), rc(4, "@ai-bot", reply=3)])
        self.assertEqual(m[0]["root_id"], 2)

    def test_bot_ignored(self):
        self.assertEqual(run([ic(1, "@ai-bot", user="AI-Bot")]), [])

    def test_permission(self):
        calls = []

        def perm(u):
            calls.append(u)
            return "write"
        c = [ic(1, "@ai-bot", assoc="CONTRIBUTOR"), ic(2, "@ai-bot", assoc="CONTRIBUTOR")]
        self.assertEqual(len(run(c, perm=perm)), 2)
        self.assertEqual(calls, ["alice"])
        self.assertEqual(run(c, perm=lambda u: "read"), [])

    def test_since(self):
        old = ic(1, "@ai-bot", at="2026-11-10T00:00:00Z")
        new = ic(2, "@ai-bot", at="2026-11-11T08:00:00Z")
        self.assertEqual([x["key"] for x in run([old, new], since="2026-11-11T00:00:00Z")],
                         ["i:2"])
        self.assertEqual(len(run([old, new])), 2)
        r = rv(3, "@ai-bot", at="2026-11-10T00:00:00Z")
        self.assertEqual(run(rvs=[r], since="2026-11-11T00:00:00Z"), [])

    def test_answered(self):
        reply = ic(10, "ok\n" + reply_marker("i:1"), user=BOT)
        threadreply = rc(11, "ok\n" + reply_marker("r:2"), user=BOT, reply=2)
        review = rv(12, "x\n" + '<!-- ai-review-reply {"c":"v:4"} -->', user=BOT)
        got = run([ic(1, "@ai-bot"), reply], [rc(2, "@ai-bot"), threadreply],
                  [rv(4, "@ai-bot"), review])
        self.assertEqual([x["key"] for x in got], ["v:4"])

    def test_forged_marker(self):
        forged = ic(10, "no mention here\n" + reply_marker("i:5"), user="mallory")
        got = run([ic(5, "@ai-bot"), forged])
        self.assertEqual([x["key"] for x in got], ["i:5"])

    def test_oldest_first(self):
        got = run([ic(1, "@ai-bot", at="2026-11-11T09:00:00Z")],
                  [rc(2, "@ai-bot", at="2026-11-11T07:00:00Z")],
                  [rv(3, "@ai-bot", at="2026-11-11T08:00:00Z")])
        self.assertEqual([x["key"] for x in got], ["r:2", "v:3", "i:1"])

    def test_thread(self):
        cs = [rc(2, "a", at="2026-11-11T07:00:00Z"), rc(4, "c", reply=2, at="2026-11-11T07:02:00Z"),
              rc(3, "b", reply=2, at="2026-11-11T07:01:00Z"), rc(9, "other")]
        self.assertEqual([c["id"] for c in fetch.thread(cs, 2)], [2, 3, 4])


if __name__ == "__main__":
    unittest.main()
