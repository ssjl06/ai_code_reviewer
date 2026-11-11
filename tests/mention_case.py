"""Shared base of the mention integration tests: a bare remote as the PR head repository,
the fake GitHub with comments, the scripted fake claude, entry `run.main`."""
import contextlib
import io
import json
import os
import subprocess

from review import rules, rules_edit, run
from review.render import parse_reply, parse_state, state_marker
from tests.fakes import make_wrapper
from tests.test_integration import IntegrationBase, git

BLOCKER = {"file": "src/k.cu", "line": 2, "severity": "major", "confidence": 85,
           "category": "memory", "title": "배열 범위를 벗어난 접근",
           "detail": "p[1]은 길이 1 버퍼에서 범위를 벗어난다."}
BLOCKING_REVIEW = {"summary": "차단 1건", "findings": [BLOCKER], "previous_findings": [],
                   "incomplete_reason": None, "diff_reviewed": True}
RULE = "기본 스트림 직후의 cudaMemcpy는 동기화 누락으로 지적하지 않는다"
PATH = "cuda/stream-sync.md"
RID = rules.rule_id(PATH, RULE)


def answer(reply="확인했습니다.", **kw):
    out = {"reply": reply, "verdicts": [], "new_issues": [], "guideline": None, "wants": None}
    out.update(kw)
    return out


def guideline(op="add", path="cuda/stream-sync.md", rule=RULE, target=None):
    g = {"op": op, "path": path, "applies": ["**/*.cu"], "rule": rule,
         "reason": "런처가 항상 기본 스트림을 쓴다"}
    if target:
        g["target"] = target
    return g


class MentionBase(IntegrationBase):
    def setUp(self):
        super().setUp()
        self.bare = os.path.join(self.tmp, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", self.bare], check=True)
        self.push_head()
        self.gh.track_remote(self.bare)
        self.script(review=None, mention=[{"out": answer()}])

    def push_head(self):
        git(self.R, "push", "-q", self.bare, "HEAD:refs/heads/feature")

    def commit_and_push(self, files):
        sha = self.add_commit(files)
        self.push_head()
        return sha

    def script(self, review=None, mention=()):
        with open(os.path.join(self.bin_dir, "mention.json"), "w", encoding="utf-8") as f:
            json.dump({"review": review, "mention": list(mention)}, f, ensure_ascii=False)

    def run_phases(self, *extra):
        binp, self.log = make_wrapper(self.bin_dir, "mscript", "mscript", name="claude-mscript")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = run.main(["--repo", self.R, "--pr", "1", "--set", "claude_bin=" + binp]
                          + list(extra))
        self.stdout, self.stderr = out.getvalue(), err.getvalue()
        return rc

    def calls(self, agent):
        if not os.path.exists(os.path.join(self.bin_dir, "calls.jsonl")):
            return []
        with open(os.path.join(self.bin_dir, "calls.jsonl"), encoding="utf-8") as f:
            rows = [json.loads(x) for x in f]
        return [r for r in rows if r["argv"][r["argv"].index("--agent") + 1] == agent]

    def mention_calls(self):
        return self.calls("pr-mention")

    def review_calls(self):
        return self.calls("pr-reviewer")

    def reviewed(self, review=BLOCKING_REVIEW):
        """A push review of the current head; returns its state."""
        self.script(review=review, mention=[{"out": answer()}])
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        return self.state()

    def state(self):
        return parse_state(self.gh.reviews[-1]["body"])

    def replies(self, key=None):
        out = [c for c in self.gh.bot_replies() if parse_reply(c["body"])]
        return [c for c in out if key is None or parse_reply(c["body"]) == key]

    def mention(self, body, **kw):
        c = self.gh.add_issue_comment(body, **kw)
        c["key"] = "i:%d" % c["id"]
        return c

    def put_state(self, state, event="COMMENT"):
        """A bot review carrying `state` (a crafted ledger)."""
        rid = len(self.gh.reviews) + 1
        self.gh.reviews.append({"id": rid, "user": {"login": "ai-bot"}, "state": event,
                                "body": "crafted\n\n" + state_marker(state),
                                "commit_id": state["sha"], "submitted_at": self.gh.now(),
                                "html_url": "https://ghe.example/r%d" % rid})

    def remote_log(self):
        return git(self.bare, "log", "feature", "--format=%s%n%b%x1e").split("\x1e")

    def trailers(self, key):
        return [c for c in self.remote_log() if "AI-Review-Mention: " + key in c]

    def with_rule(self):
        """A PR whose branch already holds the guideline RULE."""
        rules_edit.apply_change(self.R, guideline(), "PR #0 http://x (@dev, 2026-11-01, i:1)")
        rules_edit.write_indexes(self.R)
        git(self.R, "add", "-A")
        git(self.R, "commit", "-q", "-m", "rule")
        self.push_head()

    def mention_reviews(self, key):
        return [r for r in self.gh.reviews if (parse_state(r["body"]) or {}).get("m", {})
                .get("k") == key]

    def refuse_first_reply(self):
        seen = []

        def hook(body):
            seen.append(body)
            if len(seen) == 1:
                raise RuntimeError("run cancelled")
        self.gh.before_reply = hook
