import contextlib
import io
import json
import os
import unittest

from review import cli

from review.render import parse_state
from tests.test_integration import BUGGY, FIXED, IntegrationBase


class IntegrationStateTest(IntegrationBase):
    def test_dry_run_with_state_file_is_incremental(self):
        out_dir = os.path.join(self.tmp, "dry")
        self.assertEqual(self.run_main("blocking", "--repo", self.R, "--dry-run", "--base",
                                       self.BASE, "--head", self.C1, "--out-dir", out_dir), 0)
        with open(os.path.join(out_dir, "review.json"), encoding="utf-8") as f:
            state = parse_state(json.load(f)["body"])
        self.assertEqual((state["v"], state["complete"]), (3, True))
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
        # was COMMENT: the fix rewrote the flagged line, so the anchor was lost and the issue
        # "unclear". Now the issue is located, a failed run cannot confirm it, and it stays open.
        self.assertEqual(self.gh.posted[-1]["event"], "REQUEST_CHANGES")
        self.assertEqual(self.gh.dismissed, [])
        self.assertEqual(self.gh.reviews[0]["state"], "CHANGES_REQUESTED")
        st = parse_state(self.gh.posted[-1]["body"])
        self.assertEqual((st["complete"], [i["status"] for i in st["issues"]]), (False, ["open"]))


    def test_fix_in_another_file_resolves_through_the_recheck_context(self):
        self.assertEqual(self.run_main("blocking", "--repo", self.R, "--pr", "1"), 0)
        fid = parse_state(self.gh.posted[-1]["body"])["issues"][0]["id"]
        self.gh.head = self.add_commit({"src/u.cu": "int other = 2;\n"})  # k.cu untouched
        self.assertEqual(self.run_main("resolved", "--repo", self.R, "--pr", "1"), 0)
        prompt = self.fake_calls()[-1]["stdin"]
        self.assertIn("다시 확인하기 위한 현재 코드", prompt)
        self.assertIn("src/k.cu", prompt)
        self.assertIn("2:   return p[1];", prompt)
        self.assertIn('same_as', prompt)
        posted = self.gh.posted[-1]
        self.assertEqual(posted["event"], "APPROVE")
        st = parse_state(posted["body"])
        self.assertEqual([(i["id"], i["status"]) for i in st["issues"]], [(fid, "resolved")])
        self.assertEqual(st["recheck_pending"], [])

    def dry_run_state(self):
        out_dir = os.path.join(self.tmp, "dry")
        self.assertEqual(self.run_main("blocking", "--repo", self.R, "--dry-run", "--base",
                                       self.BASE, "--head", self.C1, "--out-dir", out_dir), 0)
        with open(os.path.join(out_dir, "review.json"), encoding="utf-8") as f:
            return out_dir, parse_state(json.load(f)["body"])

    def test_unknown_previous_commit_is_logged_as_weak_tracking(self):
        out_dir, state = self.dry_run_state()
        sf = os.path.join(self.tmp, "state.json")
        with open(sf, "w", encoding="utf-8") as f:
            json.dump(dict(state, sha="0" * 40), f)
        C2 = self.add_commit({"src/k.cu": BUGGY + "int z;\n"})
        self.assertEqual(self.run_main("silent", "--repo", self.R, "--dry-run", "--base",
                                       self.BASE, "--head", C2, "--state-file", sf,
                                       "--out-dir", out_dir), 0)
        self.assertIn("[main] 이전 커밋을 찾을 수 없어 위치 추적이 약해짐", self.stdout)

    def test_known_previous_commit_is_not_logged_as_weak_tracking(self):
        out_dir, state = self.dry_run_state()
        sf = os.path.join(self.tmp, "state.json")
        with open(sf, "w", encoding="utf-8") as f:
            json.dump(state, f)
        C2 = self.add_commit({"src/k.cu": BUGGY + "int z;\n"})
        self.assertEqual(self.run_main("silent", "--repo", self.R, "--dry-run", "--base",
                                       self.BASE, "--head", C2, "--state-file", sf,
                                       "--out-dir", out_dir), 0)
        self.assertNotIn("위치 추적이 약해짐", self.stdout)
        self.assertIn("다시 확인하기 위한 현재 코드", self.fake_calls()[-1]["stdin"])

    def test_pushed_state_carries_recheck_pending_only_for_open_issues(self):
        review = {"previous": [{"id": "a", "status": "open"}, {"id": "b", "status": "resolved"},
                               {"id": "c", "status": "unclear"}]}
        self.assertEqual(cli._pending(review, ["a", "b", "c", "x"]), ["a", "c"])

    def test_each_duplicate_and_link_is_logged(self):
        review = {"duplicates": [{"file": "a.cc", "line": 7, "dup_of": "i1"}],
                  "links": [{"id": "i2", "file": "b.cc", "line": 9}]}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli._log_matches(review)
            cli._log_left_out(["o1", "o2"], ["r1"])
        text = out.getvalue()
        self.assertIn("[main] 기존 이슈와 중복되어 생략: a.cc:7 (이슈 i1)", text)
        self.assertIn("[main] 기존 이슈에 연결: b.cc:9 (이슈 i2)", text)
        self.assertIn("고아 이슈 2건은 프롬프트 목록에서 제외됨: o1, o2", text)
        self.assertIn("다시 확인할 이슈 1건을 이번 실행에 담지 못함", text)
        self.assertIn("r1", text)


if __name__ == "__main__":
    unittest.main()
