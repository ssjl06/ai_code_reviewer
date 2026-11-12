import os
import subprocess
import tempfile
import unittest
from unittest import mock

from review import rules, rules_edit, rules_git

BOT = ("ai-review-bot", "bot@example.com")


def change(rule, path="cuda/a.md"):
    return {"op": "add", "path": path, "applies": ["**/*.cu"], "rule": rule, "reason": "이유",
            "target": None}


class GitRemoteCase(unittest.TestCase):
    """A work clone with a local bare remote; no git identity (HOME is a temp dir)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        p = mock.patch.dict(os.environ, {"HOME": home, "GIT_CONFIG_NOSYSTEM": "1"})
        p.start()
        self.addCleanup(p.stop)
        for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                  "GIT_COMMITTER_EMAIL", "EMAIL"):
            os.environ.pop(k, None)
        self.remote = os.path.join(self.tmp, "remote.git")
        self.work = os.path.join(self.tmp, "work")
        self.git(self.tmp, "init", "-q", "--bare", "-b", "main", self.remote)
        self.git(self.tmp, "clone", "-q", self.remote, self.work)
        self.commit(self.work, "README", "x\n", "init")
        self.git(self.work, "push", "-q", "origin", "HEAD:refs/heads/main")

    def git(self, cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                              check=True).stdout

    def commit(self, repo, rel, text, msg):
        p = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        self.git(repo, "add", "-A")
        self.git(repo, "-c", "user.name=u", "-c", "user.email=u@x", "commit", "-q", "-m", msg)

    def push(self, apply, key="i:1"):
        return rules_git.push_change(self.work, self.remote, "main", apply,
                                     "ai-review: add rule (PR #1)", key, BOT, "SECRET")

    def remote_log(self, fmt="%an|%ae|%cn|%ce|%B"):
        return self.git(self.remote, "log", "main", "--format=" + fmt + "%x1e").split("\x1e")

    def remote_file(self, rel):
        return self.git(self.remote, "show", "main:" + rules.RULES_DIR + "/" + rel)

    def on_branch(self):
        return bool(self.git(self.work, "branch", "--show-current").strip())

    def move_remote(self, rule):
        other = os.path.join(self.tmp, "other")
        self.git(self.tmp, "clone", "-q", self.remote, other)
        rules_edit.apply_change(other, change(rule), "PR #2")
        rules_edit.write_indexes(other)
        self.git(other, "add", "-A")
        self.git(other, "-c", "user.name=u", "-c", "user.email=u@x", "commit", "-q", "-m", "B")
        self.git(other, "push", "-q", "origin", "HEAD:refs/heads/main")

    def hook(self, name, body):
        p = os.path.join(self.remote, "hooks", name)
        with open(p, "w") as f:
            f.write("#!/bin/sh\n" + body)
        os.chmod(p, 0o755)
