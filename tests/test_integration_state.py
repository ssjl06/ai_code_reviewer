import json
import os
import unittest

from review.render import parse_state
from tests.test_integration import BUGGY, FIXED, IntegrationBase


class IntegrationStateTest(IntegrationBase):
    def test_dry_run_with_state_file_is_incremental(self):
        out_dir = os.path.join(self.tmp, "dry")
        self.assertEqual(self.run_main("blocking", "--repo", self.R, "--dry-run", "--base",
                                       self.BASE, "--head", self.C1, "--out-dir", out_dir), 0)
        with open(os.path.join(out_dir, "review.json"), encoding="utf-8") as f:
            state = parse_state(json.load(f)["body"])
        self.assertEqual((state["v"], state["complete"]), (2, True))
        sf = os.path.join(self.tmp, "state.json")
        with open(sf, "w", encoding="utf-8") as f:
            json.dump(state, f)
        C2 = self.add_commit({"src/k.cu": BUGGY + "int z;\n"})
        rc = self.run_main("resolved", "--repo", self.R, "--dry-run", "--base", self.BASE,
                           "--head", C2, "--state-file", sf, "--out-dir", out_dir)
        self.assertEqual(rc, 0)
        self.assertIn("증분 리뷰", self.fake_calls()[-1]["stdin"])
        self.assertNotIn("only_in_c1_marker", self.fake_calls()[-1]["stdin"])

    def test_llm_down_exit_1(self):
        self.assertEqual(self.run_main("crash", "--repo", self.R, "--pr", "1"), 1)
        posted = self.gh.posted[-1]
        self.assertEqual(posted["event"], "COMMENT")
        self.assertIn("AI 리뷰 실패", posted["body"])
        self.assertIn("### 미완료/실패 원인\n- c1: boom: crashed", posted["body"])
        st = parse_state(posted["body"])
        self.assertEqual((st["sha"], st["complete"], st["seen"]), (self.C1, False, {}))

    def test_note_alone_is_approved_and_seen(self):
        self.assertEqual(self.run_main("noted", "--repo", self.R, "--pr", "1"), 0)
        posted = self.gh.posted[-1]
        self.assertEqual(posted["event"], "APPROVE")
        self.assertIn("(참고: 탐색 예산 부족)", posted["body"])
        st = parse_state(posted["body"])
        self.assertTrue(st["complete"])
        self.assertTrue(st["seen"] or st["seen_files"])

    def test_diff_not_reviewed_stays_new_for_the_next_run(self):
        self.assertEqual(self.run_main("unreviewed", "--repo", self.R, "--pr", "1"), 0)
        posted = self.gh.posted[-1]
        self.assertEqual(posted["event"], "COMMENT")
        self.assertIn("리뷰 미완료 조각 있음", posted["body"])
        self.assertIn("- c1: 변경 줄을 읽고 판단하지 못함", posted["body"])
        st = parse_state(posted["body"])
        self.assertEqual((st["complete"], st["seen"], st["seen_files"]), (False, {}, {}))
        self.assertEqual(self.run_main("noted", "--repo", self.R, "--pr", "1"), 0)
        self.assertEqual(self.gh.posted[-1]["event"], "APPROVE")  # same head reviewed again

    def test_plain_text_json_without_diff_reviewed_is_not_seen(self):
        for scn in ("text_missing", "text_null", "text_false_str"):
            self.assertEqual(self.run_main(scn, "--repo", self.R, "--pr", "1"), 0, scn)
            posted = self.gh.posted[-1]
            self.assertEqual(posted["event"], "COMMENT", scn)
            self.assertIn("- c1: diff_reviewed 필드 누락 또는 불리언 아님", posted["body"])
            st = parse_state(posted["body"])
            self.assertEqual((st["complete"], st["seen"], st["seen_files"]), (False, {}, {}), scn)
        self.assertEqual(self.run_main("noted", "--repo", self.R, "--pr", "1"), 0)
        self.assertEqual(self.gh.posted[-1]["event"], "APPROVE")  # same head reviewed again

    def test_failed_followup_keeps_change_request(self):
        self.assertEqual(self.run_main("blocking", "--repo", self.R, "--pr", "1"), 0)
        self.gh.head = self.add_commit({"src/k.cu": FIXED})
        self.assertEqual(self.run_main("crash", "--repo", self.R, "--pr", "1"), 1)
        self.assertEqual(self.gh.posted[-1]["event"], "COMMENT")
        self.assertEqual(self.gh.dismissed, [])
        self.assertEqual(self.gh.reviews[0]["state"], "CHANGES_REQUESTED")
        st = parse_state(self.gh.posted[-1]["body"])
        self.assertEqual((st["complete"], len(st["findings"])), (False, 1))


if __name__ == "__main__":
    unittest.main()
