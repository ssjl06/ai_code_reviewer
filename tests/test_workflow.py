import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


W = _read("templates/ai-review.yml")


class WorkflowTest(unittest.TestCase):
    def test_required_settings(self):
        for s in ["shell: bash", "cancel-in-progress: true",
                  "timeout-minutes: 120", "persist-credentials: false",
                  "fetch-depth: 0", "ref: ${{ github.event.pull_request.head.sha }}",
                  "token: ${{ secrets.AI_REVIEW_BOT_PAT }}",
                  "uses: actions/upload-artifact@v3.2.2-node20",
                  "group: ai-review-${{ github.event.pull_request.number }}",
                  "cd .github/ai-review\n", "python3 -E -s -m review --repo \"$GITHUB_WORKSPACE\"",
                  "runs-on: [self-hosted]",
                  "if: always()", "retention-days: 7", "if-no-files-found: ignore"]:
            self.assertIn(s, W)
        self.assertNotIn("python3 -I -m review", W)
        # upload-artifact v4 is not supported on GHES.
        self.assertNotIn("upload-artifact@v4", W)

    def test_permissions_only_contents_read(self):
        m = re.search(r"^permissions:\n((?:  .*\n)+)", W, re.M)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "  contents: read\n")
        self.assertEqual(W.count("permissions:"), 1)
        self.assertNotIn("write", W.split("jobs:")[0].replace("# ", ""))

    def test_pull_request_trigger_only(self):
        self.assertIn("on:\n  pull_request:\n    types: [opened, synchronize, reopened]\n", W)
        for bad in ["workflow_call", "pull_request_target", "kit_repository", "kit_ref",
                    "inputs."]:
            self.assertNotIn(bad, W)

    def test_single_checkout_of_pr_head(self):
        self.assertEqual(W.count("uses: actions/checkout@v4"), 1)
        self.assertNotIn("repository:", W)
        self.assertNotIn("path: target", W)

    def test_secrets_and_settings(self):
        for s in ["AI_REVIEW_BOT_PAT", "AI_REVIEW_BOT_NAME",
                  "AI_REVIEW_LLM_API_KEY", "AI_REVIEW_LLM_BASE_URL"]:
            self.assertIn("%s: ${{ secrets.%s }}" % (s, s), W)
        env = re.search(r"^env:\n((?:  .*\n)+)", W, re.M).group(1)
        for s in ["AI_REVIEW_MODEL: deepseek-v4-flash", "MAX_CHUNKS: 30", "CONCURRENCY: 3"]:
            self.assertIn(s, env)
        self.assertIn('--set max_chunks="$MAX_CHUNKS" --set concurrency="$CONCURRENCY"', W)

    def test_no_untrusted_event_fields_in_run(self):
        for bad in ["pull_request.title", "pull_request.body", "head_ref",
                    "github.event.comment"]:
            self.assertNotIn(bad, W)

    def test_kit_repo_has_no_review_workflow(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, ".github/workflows/ai-review.yml")))


if __name__ == "__main__":
    unittest.main()
