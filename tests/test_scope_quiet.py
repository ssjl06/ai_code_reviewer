"""The quiet rule of `scope.determine` (spec 3): a push is quiet, and the open issues are not
rechecked, only when every path it touches is excluded from review."""
import unittest

from review.scope import determine
from tests.test_scope import CFG, code, run
from tests.test_scope_state import commit, issue_finding, repo_with_review


class QuietRuleTest(unittest.TestCase):
    def rechecked(self, s):
        return [p["id"] for p in s["previous"] if p.get("recheck")]

    def test_excluded_and_covered_file_in_one_push_is_not_quiet(self):
        tmp, d, base, head, state = repo_with_review([issue_finding()])
        with tmp:
            new = commit(d, {".github/ai-review-rules/cpp/x.md": "rule\n",
                             "b.cc": code("b").replace("_3 = 3", "_3 = 33")})
            s = determine(d, base, new, dict(state, approvable=True, unclear=[]), CFG)
        self.assertEqual(s["mode"], "incremental")
        self.assertEqual(self.rechecked(s), ["i1"])

    def test_rename_from_an_excluded_dir_to_a_covered_path_is_not_quiet(self):
        tmp, d, base, head, state = repo_with_review(
            [issue_finding()], files={"a.cc": code("a", 60), "b.cc": code("b"),
                                      "vendor/c.cc": code("c")})
        with tmp:
            run(d, "mv", "vendor/c.cc", "c.cc")
            run(d, "commit", "-q", "-m", "move")
            new = run(d, "rev-parse", "HEAD")
            s = determine(d, base, new, dict(state, approvable=True, unclear=[]), CFG)
        self.assertNotEqual(s["mode"], "unchanged")
        self.assertEqual(self.rechecked(s), ["i1"])


if __name__ == "__main__":
    unittest.main()
