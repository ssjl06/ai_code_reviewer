import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


W = _read(".github/workflows/ai-review.yml")


class WorkflowTest(unittest.TestCase):
    def test_required_settings(self):
        for s in ["workflow_call:", "shell: bash", "cancel-in-progress: true",
                  "timeout-minutes: 25", "persist-credentials: false",
                  "fetch-depth: 0", "pull-requests: write", "contents: read",
                  "group: ai-review-${{ github.event.pull_request.number }}",
                  "python3 -E -s -m review", "runs-on: ${{ fromJSON(inputs.runner_labels) }}",
                  "if: always()", "retention-days: 7", "if-no-files-found: ignore"]:
            self.assertIn(s, W)
        self.assertNotIn("python3 -I -m review", W)

    def test_inputs_and_secrets_declared(self):
        for s in ["AI_REVIEW_BOT_PAT", "AI_REVIEW_BOT_NAME",
                  "AI_REVIEW_LLM_API_KEY", "AI_REVIEW_LLM_BASE_URL"]:
            self.assertIn(f"{s}:\n        required: true", W)
        for s in ["model:", "max_chunks:", "concurrency:", "kit_ref:",
                  "kit_repository:", "runner_labels:", "deepseek-v4-flash"]:
            self.assertIn(s, W)

    def test_no_untrusted_event_fields_in_run(self):
        for bad in ["pull_request.title", "pull_request.body", "head_ref",
                    "github.event.comment"]:
            self.assertNotIn(bad, W)

    def test_caller_example(self):
        c = _read("docs/caller-example.yml")
        self.assertIn("types: [opened, synchronize, reopened]", c)
        self.assertIn("secrets: inherit", c)


if __name__ == "__main__":
    unittest.main()
