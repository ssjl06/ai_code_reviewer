import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from review.cli import main
from review.render import parse_state
from tests.fakes import make_wrapper
from tests.fakes.fake_github import FakeGitHub

BUGGY = "int k(int *p) {\n  return p[1];\n}\n"
FIXED = "int k(int *p) {\n  return p[0];\n}\n"
LINE_ONLY_IN_C1 = "int only_in_c1_marker = 1;"


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                          text=True).stdout.strip()


class IntegrationTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.R = os.path.join(self.tmp, "repo")
        os.makedirs(self.R)
        git(self.R, "init", "-q")
        git(self.R, "config", "user.email", "t@example.com")
        git(self.R, "config", "user.name", "t")
        self.BASE = self.add_commit({"README.md": "r\n"})
        self.C1 = self.add_commit({"src/k.cu": BUGGY, "src/u.cu": LINE_ONLY_IN_C1 + "\n"})
        self.gh = FakeGitHub(self.BASE, self.C1)
        self.addCleanup(self.gh.close)
        self.bin_dir = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin_dir)
        self.summary = os.path.join(self.tmp, "summary.md")
        env = {"GITHUB_API_URL": self.gh.url, "GITHUB_REPOSITORY": "o/r",
               "AI_REVIEW_BOT_PAT": "pat-secret", "AI_REVIEW_BOT_NAME": "ai-bot",
               "AI_REVIEW_LLM_BASE_URL": "http://llm.invalid", "AI_REVIEW_LLM_API_KEY": "k",
               "RUNNER_TEMP": self.tmp, "GITHUB_STEP_SUMMARY": self.summary,
               "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("AI_REVIEW_MODEL", None)

    def add_commit(self, files):
        for name, content in files.items():
            path = os.path.join(self.R, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            git(self.R, "add", name)
        git(self.R, "commit", "-q", "-m", "m")
        return git(self.R, "rev-parse", "HEAD")

    def run_main(self, scenario, *args):
        binp, self.log = make_wrapper(self.bin_dir, scenario, name="claude-" + scenario)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = main(list(args) + ["--set", "claude_bin=" + binp])
        self.stdout = out.getvalue()
        return rc

    def fake_calls(self):
        with open(self.log, encoding="utf-8") as f:
            return [json.loads(x) for x in f]

    def test_first_full_then_incremental(self):
        self.assertEqual(self.run_main("blocking", "--repo", self.R, "--pr", "1"), 0)
        first = self.gh.posted[-1]
        self.assertEqual(first["event"], "REQUEST_CHANGES")
        self.assertEqual(first["commit_id"], self.C1)
        state = parse_state(first["body"])
        self.assertEqual(state["sha"], self.C1)
        fid = state["findings"][0]["id"]
        self.assertEqual(first["comments"][0]["path"], "src/k.cu")
        self.assertEqual((first["comments"][0]["line"], first["comments"][0]["side"]), (2, "RIGHT"))
        self.assertIn("전체 리뷰", self.fake_calls()[0]["stdin"])
        self.assertIn("[main]", self.stdout)
        with open(self.summary, encoding="utf-8") as f:
            self.assertIn("| chunk |", f.read())
        # C2 edits src/k.cu (the fix) and adds docs/n.md; scenario "resolved"
        C2 = self.add_commit({"src/k.cu": FIXED, "docs/n.md": "x\n"})
        self.gh.head = C2
        self.assertEqual(self.run_main("resolved", "--repo", self.R, "--pr", "1"), 0)
        second = self.gh.posted[-1]
        self.assertEqual(second["event"], "APPROVE")
        self.assertEqual(parse_state(second["body"]), {"sha": C2, "findings": []})
        self.assertIn("✅ 해결", second["body"])
        self.assertEqual(self.gh.dismissed, [1])
        order = [r["method"] for r in self.gh.requests if r["method"] in ("POST", "PUT")]
        self.assertEqual(order, ["POST", "POST", "PUT"])  # new review posted before dismissal
        stdins = "\n".join(c["stdin"] for c in self.fake_calls()[1:])
        self.assertIn("증분 리뷰", stdins)
        self.assertIn('"%s"' % fid, stdins)
        self.assertNotIn(LINE_ONLY_IN_C1, stdins)  # first-commit diff not re-sent
        # third run on the same head: already reviewed, nothing posted
        self.assertEqual(self.run_main("resolved", "--repo", self.R, "--pr", "1"), 0)
        self.assertEqual(len(self.gh.posted), 2)
        self.assertIn("이미 리뷰한 커밋", self.stdout)

    def test_dry_run_posts_nothing(self):
        out_dir = os.path.join(self.tmp, "dry")
        rc = self.run_main("blocking", "--repo", self.R, "--dry-run", "--base", self.BASE,
                           "--head", self.C1, "--out-dir", out_dir)
        self.assertEqual(rc, 0)
        with open(os.path.join(out_dir, "review.json"), encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(set(data), {"event", "body", "comments"})
        self.assertEqual(data["event"], "REQUEST_CHANGES")
        self.assertEqual(self.gh.requests, [])

    def test_dry_run_with_state_file_is_incremental(self):
        sf = os.path.join(self.tmp, "state.json")
        with open(sf, "w", encoding="utf-8") as f:
            json.dump({"sha": self.BASE, "findings": []}, f)
        rc = self.run_main("resolved", "--repo", self.R, "--dry-run", "--base", self.BASE,
                           "--head", self.C1, "--state-file", sf)
        self.assertEqual(rc, 0)
        self.assertIn("증분 리뷰", self.fake_calls()[0]["stdin"])

    def test_llm_down_exit_1(self):
        self.assertEqual(self.run_main("crash", "--repo", self.R, "--pr", "1"), 1)
        posted = self.gh.posted[-1]
        self.assertEqual(posted["event"], "COMMENT")
        self.assertIn("AI 리뷰 실패", posted["body"])
        self.assertEqual(parse_state(posted["body"])["sha"], "")  # a retry reviews again

    def test_failed_followup_keeps_change_request(self):
        self.assertEqual(self.run_main("blocking", "--repo", self.R, "--pr", "1"), 0)
        self.gh.head = self.add_commit({"src/k.cu": FIXED})
        self.assertEqual(self.run_main("crash", "--repo", self.R, "--pr", "1"), 1)
        self.assertEqual(self.gh.posted[-1]["event"], "COMMENT")
        self.assertEqual(self.gh.dismissed, [])
        self.assertEqual(self.gh.reviews[0]["state"], "CHANGES_REQUESTED")
        self.assertEqual(parse_state(self.gh.posted[-1]["body"])["sha"], self.C1)

    def test_missing_env_exit_2(self):
        os.environ.pop("AI_REVIEW_BOT_PAT")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self.run_main("ok", "--repo", self.R, "--pr", "1"), 2)
        self.assertIn("AI_REVIEW_BOT_PAT", err.getvalue())
        self.assertEqual(self.gh.requests, [])

    def test_module_entry_point_help(self):
        kit = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        p = subprocess.run(["python3", "-E", "-s", "-m", "review", "--help"], cwd=kit,
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("--dry-run", p.stdout)
