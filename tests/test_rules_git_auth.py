"""The PAT on its way to git: only in the header environment of the remote commands, never in
argv, files or log lines (raw or base64), with a local and an http remote."""
import base64
import os
import subprocess
import unittest
from unittest import mock

from review import rules_git
from tests.rules_git_case import BOT, GitRemoteCase
from tests.test_rules_git import applier


class RulesGitAuthTest(GitRemoteCase):
    def test_log_masks_base64_token(self):
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

    def http_push(self, push_rc, stderr=""):
        """`push_change` to an http remote with git's network commands stubbed: (calls,
        log lines, result)."""
        url, calls, real = "http://ghe.example:8080/o/r.git", [], subprocess.run

        def spy(args, *a, **kw):
            calls.append((list(args), kw.get("env") or {}))
            if args[:2] == ["git", "push"] or args[:2] == ["git", "fetch"]:
                return subprocess.CompletedProcess(args, push_rc, "", stderr)
            return real(args, *a, **kw)
        with mock.patch("subprocess.run", spy), mock.patch.object(rules_git, "log") as logged:
            out = rules_git.push_change(self.work, url, "main", applier("규칙 A"),
                                        "ai-review: add rule (PR #1)", "i:1", BOT, "SECRET")
        return calls, [c[0][0] for c in logged.call_args_list], out

    def assert_no_secret(self, calls, lines):
        b64 = base64.b64encode(b"x-access-token:SECRET").decode()
        for argv, _ in calls:
            self.assertFalse(any(t in " ".join(argv) for t in ("SECRET", b64)), argv)
        for line in lines:
            self.assertFalse(any(t in line for t in ("SECRET", b64)), line)
        for root, _, files in os.walk(self.work):
            for name in files:
                with open(os.path.join(root, name), "rb") as f:
                    data = f.read()
                self.assertFalse(any(t.encode() in data for t in ("SECRET", b64)), name)

    def test_http_remote_pat_only_in_the_header_env(self):
        calls, lines, (res, err) = self.http_push(0)
        self.assertEqual((res["status"], err), ("added", None))
        [push] = [env for argv, env in calls if argv[1] == "push"]
        self.assertEqual(push["GIT_CONFIG_KEY_0"], "http.http://ghe.example:8080/.extraheader")
        self.assertEqual(push["GIT_CONFIG_VALUE_0"], "AUTHORIZATION: basic "
                         + base64.b64encode(b"x-access-token:SECRET").decode())
        self.assertFalse(any("GIT_CONFIG_VALUE_0" in env for argv, env in calls
                             if argv[1] not in ("push", "fetch")))
        self.assert_no_secret(calls, lines)

    def test_http_remote_error_text_masked(self):
        b64 = base64.b64encode(b"x-access-token:SECRET").decode()
        calls, lines, (res, err) = self.http_push(1, "fatal: auth %s SECRET" % b64)
        self.assertEqual((res, err), (None, "브랜치에 push하지 못했습니다"))
        self.assertTrue(lines)
        self.assert_no_secret(calls, lines)

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


if __name__ == "__main__":
    unittest.main()
