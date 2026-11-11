import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


W = _read("templates/ai-review.yml")
JOBS = W.split("\njobs:\n", 1)[1]


def _run_blocks(text):
    """The text of every `run:` block (single-line or `|`)."""
    out = []
    lines = text.split("\n")
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*)(?:- )?run:\s*(.*)$", line)
        if not m:
            continue
        if m.group(2).strip() not in ("|", ">"):
            out.append(m.group(2))
            continue
        ind = len(m.group(1))
        body = []
        for nxt in lines[i + 1:]:
            if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= ind:
                break
            body.append(nxt)
        out.append("\n".join(body))
    return "\n".join(out)


def _condition():
    return re.search(r"^    if: (.*)$", W, re.M).group(1)


class WorkflowTest(unittest.TestCase):
    def test_required_settings(self):
        for s in ["shell: bash", "cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
                  "timeout-minutes: 360", "persist-credentials: false",
                  "fetch-depth: 0", "ref: refs/pull/${{ env.PR_NUMBER }}/head",
                  "token: ${{ secrets.AI_REVIEW_BOT_PAT }}",
                  "uses: actions/upload-artifact@v3.2.2-node20",
                  "group: ${{ (",
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

    def test_triggers(self):
        head = W.split("\njobs:\n", 1)[0]
        self.assertIn("on:\n  pull_request:\n    types: [opened, synchronize, reopened, ready_for_review]\n", head)
        for ev, t in [("issue_comment", "created"), ("pull_request_review", "submitted"),
                      ("pull_request_review_comment", "created")]:
            self.assertIn("\n  %s:\n    types: [%s]\n" % (ev, t), head)
        for bad in ["workflow_call", "pull_request_target", "kit_repository", "kit_ref",
                    "inputs."]:
            self.assertNotIn(bad, W)

    def test_one_job(self):
        self.assertEqual(re.findall(r"^  (\S+):\s*$", JOBS, re.M), ["review"])

    def test_concurrency_is_per_job(self):
        self.assertNotIn("concurrency:", W.split("\njobs:\n", 1)[0])
        self.assertEqual(W.count("concurrency:"), 1)
        group = re.search(r"^      group: (.*)$", W, re.M).group(1)
        self.assertIn("format('ai-review-{0}', github.event.pull_request.number || github.event.issue.number)", group)
        self.assertIn("format('ai-review-skip-{0}', github.run_id)", group)
        self.assertIn("(" + _condition() + ")", group)
        self.assertIn("      cancel-in-progress: ${{ github.event_name == 'pull_request' }}\n", W)

    def test_job_if(self):
        c = _condition()
        for s in ["github.event.sender.login != vars.AI_REVIEW_BOT_NAME",
                  "github.event.pull_request.draft == false",
                  "github.event.issue.pull_request",
                  "contains(github.event.comment.body, format('@{0}', vars.AI_REVIEW_BOT_NAME))",
                  "contains(github.event.review.body, format('@{0}', vars.AI_REVIEW_BOT_NAME))"]:
            self.assertIn(s, c)
        self.assertEqual(c.count("contains("), 3)

    def test_checkout_ref_is_pull_head(self):
        self.assertIn("ref: refs/pull/${{ env.PR_NUMBER }}/head", W)
        self.assertIn("  PR_NUMBER: ${{ github.event.pull_request.number || github.event.issue.number }}\n", W)
        self.assertNotIn("head.sha", W)

    def test_bot_name_is_a_variable(self):
        self.assertIn("AI_REVIEW_BOT_NAME: ${{ vars.AI_REVIEW_BOT_NAME }}", W)
        self.assertNotIn("secrets.AI_REVIEW_BOT_NAME", W)

    def test_timeout_360(self):
        self.assertIn("    timeout-minutes: 360\n", W)

    def test_mention_since_setting(self):
        env = re.search(r"^env:\n((?:  .*\n)+)", W, re.M).group(1)
        self.assertIn('  MENTION_SINCE: ""\n', env)
        self.assertIn('--pr "$PR_NUMBER"', W)
        self.assertIn('--set mention_since="\\"$MENTION_SINCE\\""', W)

    def test_single_checkout_of_pr_head(self):
        self.assertEqual(W.count("uses: actions/checkout@v4"), 1)
        self.assertNotIn("repository:", W)
        self.assertNotIn("path: target", W)

    def test_draft_prs_skipped(self):
        self.assertIn("types: [opened, synchronize, reopened, ready_for_review]", W)
        self.assertIn("github.event_name == 'pull_request' && github.event.pull_request.draft == false", _condition())

    def test_secrets_and_settings(self):
        self.assertIn("AI_REVIEW_BOT_NAME: ${{ vars.AI_REVIEW_BOT_NAME }}", W)
        for s in ["AI_REVIEW_BOT_PAT",
                  "AI_REVIEW_LLM_API_KEY", "AI_REVIEW_LLM_BASE_URL"]:
            self.assertIn("%s: ${{ secrets.%s }}" % (s, s), W)
        env = re.search(r"^env:\n((?:  .*\n)+)", W, re.M).group(1)
        for s in ["AI_REVIEW_MODEL: deepseek-v4-flash", "MAX_CHUNKS: 40", "CONCURRENCY: 3"]:
            self.assertIn(s, env)
        self.assertIn('--set max_chunks="$MAX_CHUNKS" --set concurrency="$CONCURRENCY"', W)

    def test_no_untrusted_event_fields_in_run(self):
        runs = _run_blocks(W)
        self.assertIn("python3 -E -s -m review", runs)
        self.assertNotIn("${{", runs)
        for bad in ["pull_request.title", "pull_request.body", "head_ref",
                    "github.event.comment", "github.event.review"]:
            self.assertNotIn(bad, runs)

    def test_kit_repo_has_no_review_workflow(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, ".github/workflows/ai-review.yml")))


if __name__ == "__main__":
    unittest.main()
