import os
import subprocess
import unittest
from unittest import mock

from review import rules, rules_edit, rules_git
from tests.rules_git_case import GitRemoteCase, change


def applier(rule):
    def apply(repo):
        res = rules_edit.apply_change(repo, change(rule), "PR #1")
        rules_edit.write_indexes(repo)
        return res
    return apply


class RulesGitTest(GitRemoteCase):
    def test_push_lands_with_trailer_and_bot_identity(self):
        res, err = self.push(applier("규칙 A"))
        self.assertIsNone(err)
        self.assertEqual(res["status"], "added")
        top = self.remote_log()[0]
        self.assertTrue(top.startswith("ai-review-bot|bot@example.com|ai-review-bot|bot@example.com|"))
        self.assertIn("ai-review: add rule (PR #1)\n\nAI-Review-Mention: i:1", top)

    def test_nothing_changed_no_commit(self):
        self.push(applier("규칙 A"))
        n = len(self.remote_log())
        res, err = self.push(applier("규칙 A"), key="i:2")
        self.assertEqual((res["status"], err), ("unchanged", None))
        self.assertEqual(len(self.remote_log()), n)

    def test_concurrent_edit_same_file_kept(self):
        other = os.path.join(self.tmp, "other")
        self.git(self.tmp, "clone", "-q", self.remote, other)
        rules_edit.apply_change(other, change("규칙 B"), "PR #2")
        rules_edit.write_indexes(other)
        self.git(other, "add", "-A")
        self.git(other, "-c", "user.name=u", "-c", "user.email=u@x", "commit", "-q", "-m", "B")
        self.git(other, "push", "-q", "origin", "HEAD:refs/heads/main")
        branch_before = self.git(self.work, "rev-parse", "main").strip() if self.on_branch() else None
        res, err = self.push(applier("규칙 A"))
        self.assertIsNone(err)
        if branch_before:
            self.assertEqual(self.git(self.work, "rev-parse", "main").strip(), branch_before)
        text = self.remote_file("cuda/a.md")
        self.assertIn("규칙 A", text)
        self.assertIn("규칙 B", text)
        index = self.remote_file("cuda/INDEX.md")
        self.assertIn("cuda/a.md", index)

    def test_failed_retry_moves_no_branch_ref(self):
        self.assertTrue(self.on_branch())
        start = self.git(self.work, "rev-parse", "HEAD").strip()
        self.move_remote("규칙 B")
        # reject only the retry: the first push is non-fast-forward anyway
        self.hook("pre-receive", "exit 1\n")
        with mock.patch.object(rules_git, "log"):
            res, err = self.push(applier("규칙 A"))
        self.assertEqual((res, err), (None, "브랜치에 push하지 못했습니다"))
        self.assertEqual(self.git(self.work, "rev-parse", "HEAD").strip(), start)
        self.assertEqual(self.git(self.work, "rev-parse", "main").strip(), start)
        self.assertEqual(self.git(self.work, "status", "--porcelain"), "")

    def test_apply_raises_cleans_and_reraises(self):
        start = self.git(self.work, "rev-parse", "HEAD").strip()

        def boom(repo):
            applier("규칙 A")(repo)
            raise ValueError("x")
        with self.assertRaises(ValueError):
            self.push(boom)
        self.assertEqual(self.git(self.work, "rev-parse", "HEAD").strip(), start)
        self.assertEqual(self.git(self.work, "status", "--porcelain"), "")

    def test_landed_then_another_commit_on_top(self):
        self.hook("post-receive", "exit 1\n")
        real = rules_git._run

        def run(repo, args, env=None, timeout=120):
            out = real(repo, args, env, timeout)
            if args[0] == "push":
                self.move_remote("규칙 C")
                raise rules_git.GitError("reported error")
            return out
        with mock.patch.object(rules_git, "_run", run):
            res, err = self.push(applier("규칙 A"))
        self.assertIsNone(err)
        self.assertEqual(res["status"], "added")

    def test_log_masks_base64_token(self):
        import base64
        b64 = base64.b64encode(b"x-access-token:SECRET").decode()
        real = rules_git._run

        def run(repo, args, env=None, timeout=120):
            if args[0] == "push":
                raise rules_git.GitError("bad " + b64 + " SECRET")
            return real(repo, args, env, timeout)
        with mock.patch.object(rules_git, "_run", run), \
                mock.patch.object(rules_git, "log") as logged:
            self.push(applier("규칙 A"))
        text = logged.call_args[0][0]
        self.assertNotIn("SECRET", text)
        self.assertNotIn(b64, text)

    def test_push_landed_but_reported_error(self):
        self.hook("post-receive", "exit 1\n")
        res, err = self.push(applier("규칙 A"))
        self.assertIsNone(err)
        self.assertEqual(res["status"], "added")
        self.assertEqual(len([r for r in self.remote_log() if "AI-Review-Mention" in r]), 1)

    def test_final_failure_leaves_clean_tree(self):
        self.hook("pre-receive", "exit 1\n")
        before = self.git(self.work, "rev-parse", "HEAD")
        with mock.patch.object(rules_git, "log") as logged:
            res, err = self.push(applier("규칙 A"))
        self.assertTrue(logged.called)
        self.assertEqual((res, err), (None, "브랜치에 push하지 못했습니다"))
        self.assertEqual(self.git(self.work, "rev-parse", "HEAD"), before)
        self.assertEqual(self.git(self.work, "status", "--porcelain"), "")
        self.assertFalse(os.path.exists(os.path.join(self.work, rules.RULES_DIR)))

    def test_old_git(self):
        before = self.git(self.work, "rev-parse", "HEAD")
        with mock.patch.object(rules_git, "git_ok", return_value=False):
            res, err = self.push(applier("규칙 A"))
        self.assertEqual((res, err), (None, "git 2.31 이상이 필요합니다"))
        self.assertEqual(self.git(self.work, "rev-parse", "HEAD"), before)
        self.assertFalse(os.path.exists(os.path.join(self.work, rules.RULES_DIR)))

    def test_pat_never_in_argv_or_config(self):
        calls = []
        real = subprocess.run

        def spy(args, *a, **kw):
            calls.append((list(args), kw.get("env")))
            return real(args, *a, **kw)
        with mock.patch("subprocess.run", spy):
            self.push(applier("규칙 A"))
        self.assertTrue(calls)
        for argv, env in calls:
            self.assertNotIn("SECRET", " ".join(argv))
            self.assertFalse(env and any("SECRET" in v for v in env.values()))
        with open(os.path.join(self.work, ".git", "config")) as f:
            self.assertNotIn("SECRET", f.read())

    def test_pat_only_in_remote_commands(self):
        calls = []
        real = subprocess.run

        def spy(args, *a, **kw):
            env = kw.get("env") or {}
            calls.append((args[1], "GIT_CONFIG_VALUE_0" in env))
            return real(args, *a, **kw)
        with mock.patch("subprocess.run", spy):
            self.push(applier("규칙 A"))
        self.assertTrue(any(c == ("push", False) for c in calls))  # file remote: no header
        self.assertFalse(any(has for c, has in calls if c in ("commit", "add", "log")))

    def test_header_scoped_to_origin(self):
        env = rules_git.auth_env("tok", "https://ghe.example.com", {"PATH": "/bin"})
        self.assertEqual(env["GIT_CONFIG_COUNT"], "1")
        self.assertEqual(env["GIT_CONFIG_KEY_0"], "http.https://ghe.example.com/.extraheader")
        self.assertEqual(env["GIT_CONFIG_VALUE_0"],
                         "AUTHORIZATION: basic eC1hY2Nlc3MtdG9rZW46dG9r")
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(rules_git.origin_of("https://u:p@ghe.example.com:8443/o/r.git"),
                         "https://ghe.example.com:8443")
        self.assertEqual(rules_git.origin_of("/tmp/remote.git"), "")
        self.assertNotIn("GIT_CONFIG_COUNT", rules_git.auth_env("tok", "", {}))

    def test_trace_vars_removed(self):
        env = rules_git.auth_env("tok", "https://h", {
            "GIT_TRACE": "1", "GIT_TRACE_CURL": "1", "GIT_CURL_VERBOSE": "1", "HOME": "/h"})
        self.assertEqual(sorted(k for k in env if k.startswith("GIT_TRACE") or k == "GIT_CURL_VERBOSE"), [])
        self.assertEqual(env["HOME"], "/h")

    def test_receipt(self):
        self.push(applier("규칙 A"), key="r:123")
        head = self.git(self.work, "rev-parse", "HEAD").strip()
        base = self.git(self.work, "rev-parse", "HEAD~1").strip()
        self.assertEqual(rules_git.receipt(self.work, base, head, "r:123"),
                         "ai-review: add rule (PR #1)")
        self.assertIsNone(rules_git.receipt(self.work, base, head, "r:12"))
        self.commit(self.work, "z", "z\n", "subject has r:77 only")
        head2 = self.git(self.work, "rev-parse", "HEAD").strip()
        self.assertIsNone(rules_git.receipt(self.work, head, head2, "r:77"))

    def test_receipt_for_removal(self):
        self.push(applier("규칙 A"))

        def remove(repo):
            rid = rules.rule_id("cuda/a.md", "규칙 A")
            c = {"op": "remove", "path": "cuda/a.md", "target": rid}
            res = rules_edit.apply_change(repo, c, "PR #1")
            rules_edit.write_indexes(repo)
            return res
        res, err = self.push(remove, key="i:9")
        self.assertEqual((res["status"], err), ("removed", None))
        head = self.git(self.work, "rev-parse", "HEAD").strip()
        self.assertIsNotNone(rules_git.receipt(self.work, head + "~1", head, "i:9"))


if __name__ == "__main__":
    unittest.main()
