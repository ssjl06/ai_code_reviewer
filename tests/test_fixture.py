import json
import os
import subprocess
import tempfile
import unittest

from tests.fakes import make_wrapper

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(KIT, "examples", "buggy-pr", "make_repo.sh")
FILES = ["docs/usage.md", "scripts/clean.sh", "src/scale.cu", "tools/run.py"]


class FixtureTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.repo = os.path.join(self.tmp, "repo")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True,
                              capture_output=True, text=True).stdout

    def test_make_repo_and_dry_run(self):
        out = subprocess.run([SCRIPT, self.repo], check=True, capture_output=True, text=True)
        self.assertIn("main=", out.stdout)
        self.assertIn("feature=", out.stdout)
        branches = self.git("branch", "--format=%(refname:short)").split()
        self.assertEqual(sorted(branches), ["feature", "main"])
        self.assertEqual(self.git("diff", "main...feature", "--name-only").split(), FILES)

        bin_dir = os.path.join(self.tmp, "bin")
        os.makedirs(bin_dir)
        wrapper, _ = make_wrapper(bin_dir, "blocking")
        out_dir = os.path.join(self.tmp, "out")
        env = dict(os.environ, AI_REVIEW_LLM_BASE_URL="http://llm.invalid",
                   AI_REVIEW_LLM_API_KEY="k")
        r = subprocess.run(
            ["python3", "-E", "-s", "-m", "review", "--dry-run", "--repo", self.repo,
             "--base", "main", "--head", "feature", "--out-dir", out_dir,
             "--set", "claude_bin=" + wrapper],
            cwd=KIT, env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(out_dir, "review.json"), encoding="utf-8") as f:
            review = json.load(f)
        for key in ("event", "body", "comments"):
            self.assertIn(key, review)


if __name__ == "__main__":
    unittest.main()
