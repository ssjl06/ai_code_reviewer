"""Mention phase end to end: commands, conversation, new issues."""
import unittest

from review.render import parse_issue_marker, parse_state
from tests.mention_case import BLOCKER, BLOCKING_REVIEW, MentionBase, answer


class MentionCommandTest(MentionBase):
    def test_withdraw_command_approves(self):
        st = self.reviewed()
        fid = st["issues"][0]["id"]
        self.assertEqual(self.gh.reviews[0]["state"], "CHANGES_REQUESTED")
        m = self.mention("@ai-bot /철회 %s 테스트 코드라 의도된 동작" % fid)
        self.script(mention=[{"out": answer("철회합니다."), "withdraw_all": True}])
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        posted = self.gh.posted[-1]
        self.assertEqual(posted["event"], "APPROVE")
        self.assertEqual(posted["commit_id"], self.C1)
        new = parse_state(posted["body"])
        self.assertEqual([(i["id"], i["status"]) for i in new["issues"]], [(fid, "waived")])
        self.assertEqual(new["m"], {"k": m["key"], "w": [fid], "n": [], "up": []})
        self.assertEqual(self.gh.dismissed, [1])
        [put] = [r for r in self.gh.requests if r["method"] == "PUT"]
        self.assertEqual(put["body"]["message"], "코멘트를 반영하여 이전 AI 변경 요청을 해제합니다.")
        [reply] = self.replies(m["key"])
        self.assertIn("철회합니다.", reply["body"])
        self.assertIn("**반영:** 철회 %s" % fid, reply["body"])
        self.assertTrue(reply["body"].startswith("> @\u200bai-bot /철회 %s 테스트 코드라" % fid))
        self.assertEqual(len(self.mention_calls()), 1)
        self.assertIn("[m1]", self.stdout)

    def test_negated_plain_words_do_not_withdraw(self):
        self.reviewed()
        m = self.mention("@ai-bot 철회 안 해도 돼, 진짜 버그야")
        self.script(mention=[{"out": answer("네, 유지합니다."), "withdraw_all": True}])
        n = len(self.gh.posted)
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        self.assertEqual(len(self.gh.posted), n)  # no review: nothing changed
        self.assertEqual(self.state()["issues"][0]["status"], "open")
        [reply] = self.replies(m["key"])
        self.assertIn("**반영:** 없음", reply["body"])

    def test_unknown_command_lists_commands(self):
        self.reviewed()
        m = self.mention("@ai-bot /취소 해줘")
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        [reply] = self.replies(m["key"])
        self.assertIn("알 수 없는 명령입니다. 쓸 수 있는 명령: `/철회`, `/지침`, `/지침수정`, "
                      "`/지침삭제`", reply["body"])
        self.assertEqual(self.mention_calls(), [])

    def test_no_command_reply_only(self):
        self.reviewed()
        m = self.mention("@ai-bot 이건 의도된 거야")
        self.script(mention=[{"out": answer("의도된 동작이면 괜찮습니다."), "withdraw_all": True}])
        n = len(self.gh.posted)
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        self.assertEqual(len(self.gh.posted), n)
        [reply] = self.replies(m["key"])
        self.assertIn("의도된 동작이면 괜찮습니다.", reply["body"])
        self.assertIn("**반영:** 없음", reply["body"])

    def test_new_issue_on_diff_line_requests_changes(self):
        self.reviewed(review=None)  # APPROVE, no issues
        self.assertEqual(self.gh.posted[-1]["event"], "APPROVE")
        m = self.mention("@ai-bot 2번 줄 범위 넘는 거 아냐?")
        self.script(mention=[{"out": answer("맞습니다, 범위를 벗어납니다.", new_issues=[BLOCKER])}])
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        posted = self.gh.posted[-1]
        self.assertEqual(posted["event"], "REQUEST_CHANGES")
        st = parse_state(posted["body"])
        [issue] = st["issues"]
        self.assertTrue(issue["blocking"])
        [c] = posted["comments"]
        self.assertEqual((c["path"], c["line"], c["side"]), ("src/k.cu", 2, "RIGHT"))
        self.assertEqual(parse_issue_marker(c["body"]), issue["id"])
        self.assertIn("### 답변\n맞습니다, 범위를 벗어납니다.", posted["body"])
        self.assertNotIn("ai-review-issue", posted["body"])
        [reply] = self.replies(m["key"])
        self.assertIn("새 이슈 %s (차단)" % issue["id"], reply["body"])


    def test_plain_request_gets_hint(self):
        self.reviewed()
        m = self.mention("@ai-bot 이거 철회해 줘")
        self.script(mention=[{"out": answer("철회를 원하시는군요.", wants="withdraw")}])
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        [reply] = self.replies(m["key"])
        self.assertIn("반영하려면 `@ai-bot /철회`로 불러 주세요.", reply["body"])
        self.assertEqual(self.state()["issues"][0]["status"], "open")

    def test_single_open_issue_is_the_target(self):
        fid = self.reviewed()["issues"][0]["id"]
        self.mention("@ai-bot /철회 의도된 동작입니다")
        self.script(mention=[{"out": answer("철회합니다."), "withdraw_all": True}])
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        self.assertIn("[m1] 철회 대상: " + fid, self.stdout)
        self.assertEqual(self.state()["issues"][0]["status"], "waived")

    def test_two_open_issues_asks_which(self):
        two = dict(BLOCKING_REVIEW, findings=[BLOCKER, dict(BLOCKER, file="src/u.cu", line=1,
                                                             title="전역 변수")])
        st = self.reviewed(review=two)
        self.assertEqual(len(st["issues"]), 2)
        m = self.mention("@ai-bot /철회 둘 다 의도된 거야")
        n = len(self.gh.posted)
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        self.assertEqual(self.mention_calls(), [])
        self.assertEqual(len(self.gh.posted), n)
        [reply] = self.replies(m["key"])
        self.assertIn("어느 이슈인지 알 수 없습니다.", reply["body"])
        for i in st["issues"]:
            self.assertIn("`%s` `%s:%s`" % (i["id"], i["file"], i["line"]), reply["body"])


class MentionLedgerTest(MentionBase):
    """Focus 4 and 5: a withdrawal never approves what the review could not, and a new issue
    the ledger cannot take is a note, never a crash."""

    def crafted(self, **changes):
        st = self.reviewed()
        st.update(changes)
        self.gh.draft = True  # the mention phase alone works on the crafted ledger
        self.put_state(st)
        return st

    def withdraw_all(self, body="@ai-bot /철회 의도된 동작"):
        m = self.mention(body)
        self.script(mention=[{"out": answer("철회합니다."), "withdraw_all": True}])
        return m

    def test_withdraw_when_review_had_no_targets_comments(self):
        self.crafted(approvable=False)  # what merge writes for a review without targets
        self.withdraw_all()
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        self.assertIn("리뷰 단계 건너뜀: 초안", self.stdout)
        posted = self.gh.posted[-1]
        self.assertEqual(posted["event"], "COMMENT")
        self.assertEqual(parse_state(posted["body"])["issues"][0]["status"], "waived")
        self.assertEqual(self.gh.dismissed, [])

    def test_withdraw_with_cut_file_comments(self):
        self.commit_and_push({"src/long.cu": "int x = %s;\n" % ("1 + " * 200 + "1")})
        cut = ("--set", "chunk_max_tokens=200")
        self.script(review=BLOCKING_REVIEW)
        self.assertEqual(self.run_phases(*cut), 0, self.stdout + self.stderr)
        st = self.state()
        self.assertTrue(st.get("cut"), st)
        self.assertFalse(st["approvable"])
        self.withdraw_all()
        self.assertEqual(self.run_phases(*cut), 0, self.stdout + self.stderr)
        posted = self.gh.posted[-1]
        self.assertEqual(posted["event"], "COMMENT")
        self.assertEqual(parse_state(posted["body"])["m"]["w"], [st["issues"][0]["id"]])

    def test_unrelated_orphan_stays_blocking(self):
        st = self.reviewed()
        target = st["issues"][0]
        orphan = dict(target, id="0rphan01", file="src/gone.cu", line=7, lo=7, hi=7,
                      orphan=True, title="다른 차단 이슈")
        # current for the head (an incomplete state's stored blockers would not decide, F3)
        st.update(approvable=False, issues=[target, orphan])
        self.gh.draft = True
        self.put_state(st)
        self.withdraw_all("@ai-bot /철회 %s 의도된 동작" % target["id"])
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        posted = self.gh.posted[-1]
        self.assertEqual(posted["event"], "REQUEST_CHANGES")
        new = {i["id"]: i for i in parse_state(posted["body"])["issues"]}
        self.assertEqual(new[target["id"]]["status"], "waived")
        self.assertEqual((new["0rphan01"]["status"], new["0rphan01"]["blocking"]), ("open", True))

    def test_old_marker_stays_comment(self):
        st = self.reviewed(review=None)
        del st["unclear"], st["approvable"]  # written before this feature
        self.gh.draft = True
        self.put_state(st, event="COMMENTED")
        self.mention("@ai-bot 2번 줄도 봐 줘")
        info = dict(BLOCKER, severity="info", title="참고 사항")
        self.script(mention=[{"out": answer("참고로 남깁니다.", new_issues=[info])}])
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        posted = self.gh.posted[-1]
        self.assertEqual(posted["event"], "COMMENT")
        new = parse_state(posted["body"])
        self.assertNotIn("unclear", new)
        self.assertEqual(len(new["m"]["n"]), 1)

    def test_line_outside_hunks_note_only(self):
        big = "".join("int v%d = %d;\n" % (k, k) for k in range(1, 31))
        c2 = self.commit_and_push({"src/big.cu": big})
        self.gh.base = c2
        self.commit_and_push({"src/big.cu": big.replace("v1 = 1", "v1 = 2")})
        self.reviewed(review=None)
        m = self.mention("@ai-bot 25번 줄 확인해 줘")
        far = dict(BLOCKER, file="src/big.cu", line=25)
        self.script(mention=[{"out": answer("문제가 있습니다.", new_issues=[far])}])
        n = len(self.gh.posted)
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        self.assertEqual(len(self.gh.posted), n)
        [reply] = self.replies(m["key"])
        self.assertIn("- diff 밖이라 이슈로 등록하지 않음", reply["body"])
        self.assertIn("문제가 있습니다.", reply["body"])

    def test_issue_in_excluded_file_note_only(self):
        self.commit_and_push({"vendor/lib.c": "int v = 0;\n"})
        self.reviewed(review=None)
        m = self.mention("@ai-bot vendor 코드도 봐 줘")
        vend = dict(BLOCKER, file="vendor/lib.c", line=1)
        self.script(mention=[{"out": answer("확인했습니다.", new_issues=[vend])}])
        n = len(self.gh.posted)
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        self.assertEqual(len(self.gh.posted), n)
        [reply] = self.replies(m["key"])
        self.assertIn("- 리뷰 대상이 아닌 파일이라 이슈로 등록하지 않음", reply["body"])

    def test_mention_without_any_state(self):
        self.gh.draft = True
        m = self.mention("@ai-bot 이 커널 뭐 하는 거야?")
        self.script(mention=[{"out": answer("배열을 읽습니다.", new_issues=[BLOCKER])}])
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        self.assertEqual(self.gh.posted, [])
        self.assertEqual(self.review_calls(), [])
        [reply] = self.replies(m["key"])
        self.assertIn("- 아직 리뷰가 없어 이슈로 등록하지 않음", reply["body"])


if __name__ == "__main__":
    unittest.main()
