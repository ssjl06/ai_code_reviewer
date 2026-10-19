import os

from review.render import parse_state
from tests.test_integration import BUGGY, FIXED, LINE_ONLY_IN_C1, IntegrationBase, git


class ForcePushTest(IntegrationBase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.run_main("blocking", "--repo", self.R, "--pr", "1"), 0)
        self.first = self.gh.posted[0]

    def force(self, files, drop=()):
        """Squash the PR into one new commit on BASE, as a force-push would."""
        git(self.R, "reset", "-q", "--soft", self.BASE)
        for name, content in files.items():
            with open(os.path.join(self.R, name), "w", encoding="utf-8") as f:
                f.write(content)
            git(self.R, "add", name)
        for name in drop:
            git(self.R, "rm", "-q", "--cached", name)
            os.remove(os.path.join(self.R, name))
        git(self.R, "commit", "-q", "-m", "squash")
        self.gh.head = git(self.R, "rev-parse", "HEAD")
        return self.gh.head

    def review(self, scenario):
        return self.run_main(scenario, "--repo", self.R, "--pr", "1")

    def test_squash_no_llm_call_nothing_posted(self):
        new = self.force({})
        self.assertNotEqual(new, self.C1)
        calls = len(self.fake_calls())
        self.assertEqual(self.review("silent"), 0)
        self.assertEqual((len(self.fake_calls()), len(self.gh.posted)), (calls, 1))
        self.assertIn("새로 리뷰할 변경 없음", self.stdout)

    def test_rebase_shift_keeps_block_same_id(self):
        first_id = parse_state(self.first["body"])["findings"][0]["id"]
        self.force({"src/k.cu": "// pad\n" * 10 + BUGGY})
        self.assertEqual(self.review("silent"), 0)
        st = parse_state(self.gh.posted[-1]["body"])
        self.assertEqual(self.gh.posted[-1]["event"], "REQUEST_CHANGES")
        self.assertEqual([(f["id"], f["line"]) for f in st["findings"]], [(first_id, 12)])

    def test_fix_resolved_approves_and_dismisses(self):
        self.force({"src/k.cu": FIXED})
        self.assertEqual(self.review("resolved"), 0)
        self.assertEqual(self.gh.posted[-1]["event"], "APPROVE")
        self.assertEqual(self.gh.dismissed, [1])

    def test_anchor_changed_model_silent_comment(self):
        self.force({"src/k.cu": BUGGY.replace("p[1]", "p[2]")})
        self.assertEqual(self.review("silent"), 0)
        self.assertEqual(self.gh.posted[-1]["event"], "COMMENT")
        self.assertIn("이전 차단 지적 확인 필요", self.gh.posted[-1]["body"])
        self.assertEqual(self.gh.dismissed, [])

    def test_file_dropped_resolves_and_approves(self):
        self.force({}, drop=["src/k.cu"])
        calls = len(self.fake_calls())
        self.assertEqual(self.review("silent"), 0)
        self.assertEqual(len(self.fake_calls()), calls)
        self.assertEqual(self.gh.posted[-1]["event"], "APPROVE")
        self.assertEqual(self.gh.dismissed, [1])

    def test_merge_main_no_review(self):
        pr_branch = git(self.R, "rev-parse", "--abbrev-ref", "HEAD")
        git(self.R, "checkout", "-q", "-b", "newmain", self.BASE)
        main_tip = self.add_commit({"m.py": "m = 1\n"})
        git(self.R, "checkout", "-q", pr_branch)
        git(self.R, "merge", "-q", "--no-ff", "-m", "merge main", "newmain")
        self.gh.head = git(self.R, "rev-parse", "HEAD")
        self.gh.base = main_tip
        calls = len(self.fake_calls())
        self.assertEqual(self.review("silent"), 0)
        self.assertEqual((len(self.fake_calls()), len(self.gh.posted)), (calls, 1))
        self.assertIn("새로 리뷰할 변경 없음", self.stdout)

    def test_deletion_reviewed(self):
        self.gh.head = self.add_commit({"README.md": "kept\n"})  # C2 deletes base line "r"
        self.assertEqual(self.review("silent"), 0)
        self.assertIn("삭제 지점:\n- README.md: 1", self.fake_calls()[-1]["stdin"])


if __name__ == "__main__":
    import unittest
    unittest.main()
