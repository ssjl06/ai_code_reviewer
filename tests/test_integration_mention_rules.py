"""Mention phase end to end: guideline commands and recovery after a run died (Focus 3)."""
import json
import subprocess
import unittest

from review import rules
from review.render import parse_state
from tests.mention_case import BLOCKER, PATH, RID, RULE, MentionBase, answer, guideline
from tests.test_integration import FIXED, git


class GuidelineTest(MentionBase):
    def test_rule_command_commits(self):
        fid = self.reviewed()["issues"][0]["id"]
        m = self.mention("@ai-bot /지침 기본 스트림 직후 cudaMemcpy는 지적하지 마")
        self.script(mention=[{"out": answer("지침으로 남깁니다.", guideline=guideline())}])
        reviews_before, posted = len(self.review_calls()), len(self.gh.posted)
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        [commit] = self.trailers(m["key"])
        self.assertTrue(commit.strip().startswith(
            "ai-review: add rule %s to %s (PR #1)" % (RID, PATH)), commit)
        text = git(self.bare, "show", "feature:%s/%s" % (rules.RULES_DIR, PATH))
        self.assertIn("## %s  %s" % (RID, RULE), text)
        self.assertIn("- 출처: PR #1 %s (@dev, 2026-11-11, %s)" % (m["html_url"], m["key"]), text)
        [reply] = self.replies(m["key"])
        self.assertIn("지침 %s %s 추가" % (PATH, RID), reply["body"])
        self.assertIn("- 브랜치에 커밋을 추가했습니다. 다음 push 전에 pull 하세요.", reply["body"])
        self.assertEqual(len(self.gh.posted), posted)  # no ledger change: no review
        self.assertEqual(len(self.review_calls()), reviews_before)
        # the PAT reached neither the model, the log, the reply nor .git/config
        self.assertNotIn("AI_REVIEW_BOT_PAT", self.mention_calls()[0]["env_keys"])
        self.assertNotIn("pat-secret", json.dumps(self.mention_calls()))  # argv and prompt
        for text in (self.stdout, self.stderr, reply["body"], git(self.R, "config", "--list")):
            self.assertNotIn("pat-secret", text)
        # a later /철회 of the only blocker approves on the guideline commit
        g_head = git(self.R, "rev-parse", "HEAD")
        self.assertEqual(self.gh.head, g_head)
        self.mention("@ai-bot /철회 의도된 동작")
        self.script(mention=[{"out": answer("철회합니다."), "withdraw_all": True}])
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        last = self.gh.posted[-1]
        self.assertEqual((last["event"], last["commit_id"]), ("APPROVE", g_head))
        st = parse_state(last["body"])
        self.assertEqual((st["sha"], st["issues"][0]["status"]), (self.C1, "waived"))
        self.assertEqual(st["issues"][0]["id"], fid)
        self.assertEqual(len(self.review_calls()), reviews_before)  # the review phase: unchanged
        # the next push review reads the rule
        self.commit_and_push({"src/k.cu": FIXED})
        self.script(review=None)
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        self.assertIn(RULE, self.review_calls()[-1]["stdin"])

    def test_review_after_push_is_on_the_pushed_commit(self):
        self.reviewed()
        m1 = self.mention("@ai-bot /지침 기본 스트림 직후 cudaMemcpy는 지적하지 마")
        m2 = self.mention("@ai-bot 고마워")
        extra = dict(BLOCKER, file="src/u.cu", line=1, title="전역 변수 사용")
        self.script(mention=[{"when": "고마워", "out": answer("천만에요.")},
                             {"out": answer("남깁니다.", guideline=guideline(),
                                            new_issues=[extra])}])
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        pushed = git(self.R, "rev-parse", "HEAD")
        self.assertNotEqual(pushed, self.C1)
        self.assertEqual(self.gh.posted[-1]["commit_id"], pushed)
        self.assertEqual(len(self.replies(m1["key"]) + self.replies(m2["key"])), 2)

    def test_rule_add_with_model_remove_commits_nothing(self):
        self.reviewed()
        m = self.mention("@ai-bot /지침 기본 스트림은 동기화된다")
        self.script(mention=[{"out": answer("알겠습니다.", guideline=guideline(
            "remove", target="R-000000"))}])
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        self.assertEqual(self.gh.head, self.C1)
        [reply] = self.replies(m["key"])
        self.assertIn("- 반영 안 됨: 명령과 다른 지침 작업", reply["body"])

    def test_rule_remove_quotes_old_text(self):
        self.with_rule()
        self.reviewed()
        m = self.mention("@ai-bot /지침삭제 cudaMemcpy 지침은 이제 필요 없어")
        self.script(mention=[{"out": answer("삭제합니다.", guideline=guideline(
            "remove", target=RID))}])
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        [commit] = self.trailers(m["key"])
        self.assertIn("ai-review: remove rule %s from %s (PR #1)" % (RID, PATH), commit)
        p = subprocess.run(["git", "--git-dir", self.bare, "cat-file", "-e",
                            "feature:%s/%s" % (rules.RULES_DIR, PATH)], capture_output=True)
        self.assertNotEqual(p.returncode, 0)  # the last rule removed the file
        [reply] = self.replies(m["key"])
        self.assertIn('이전 지침 내용: "%s"' % RULE, reply["body"])
        self.assertIn("지침 %s %s 삭제" % (PATH, RID), reply["body"])

    def test_full_file_moves_the_rule_to_a_new_file(self):
        full = {"applies": ["**/*.cu"], "title": "가득 찬 파일", "rules": [
            {"id": rules.rule_id(PATH, "규칙 %d" % k), "text": "규칙 %d" % k, "reason": "이유",
             "source": "PR #0"} for k in range(32)]}
        self.assertEqual(rules.render_doc(full).count("\n"), 100)
        self.commit_and_push({"%s/%s" % (rules.RULES_DIR, PATH): rules.render_doc(full)})
        self.reviewed()
        m = self.mention("@ai-bot /지침 기본 스트림 직후 cudaMemcpy는 지적하지 마")
        moved = dict(guideline(), path="cuda/memcpy.md")
        self.script(mention=[{"when": "새 주제 파일 경로", "out": answer("옮깁니다.", guideline=moved)},
                             {"out": answer("남깁니다.", guideline=guideline())}])
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        rid = rules.rule_id("cuda/memcpy.md", RULE)
        [commit] = self.trailers(m["key"])
        self.assertIn("ai-review: add rule %s to cuda/memcpy.md (PR #1)" % rid, commit)
        self.assertIn(RULE, git(self.bare, "show", "feature:%s/cuda/memcpy.md" % rules.RULES_DIR))
        self.assertEqual(len(self.mention_calls()), 2)  # the session and the path resume

    def test_fork_pr_gives_the_entry_to_commit(self):
        self.reviewed()
        self.gh.full_name = "someone/r"
        m = self.mention("@ai-bot /지침 기본 스트림은 동기화된다")
        self.script(mention=[{"out": answer("남깁니다.", guideline=guideline())}])
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        self.assertEqual(self.gh.head, self.C1)
        [reply] = self.replies(m["key"])
        self.assertIn("- 반영 안 됨: 지침 %s: PR 브랜치가 이 저장소에 없어" % PATH, reply["body"])
        self.assertIn("## %s  %s" % (RID, RULE), reply["body"])


class RecoveryTest(MentionBase):
    def test_recovery_after_push_before_review(self):
        self.reviewed()
        m = self.mention("@ai-bot /지침 기본 스트림 직후 cudaMemcpy는 지적하지 마")
        extra = dict(BLOCKER, file="src/u.cu", line=1, title="전역 변수 사용")
        self.script(mention=[{"out": answer("남기고 이슈도 등록합니다.", guideline=guideline(),
                                            new_issues=[extra])}])
        self.gh.fail_next_post(403, "/reviews")
        self.assertEqual(self.run_phases(), 1)
        self.assertEqual(len(self.trailers(m["key"])), 1)
        self.assertEqual(self.replies(), [])
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        self.assertEqual(len(self.trailers(m["key"])), 1)  # no second commit
        [review] = self.mention_reviews(m["key"])
        self.assertEqual(parse_state(review["body"])["m"]["g"], "%s %s 추가" % (PATH, RID))
        [reply] = self.replies(m["key"])
        self.assertIn("지침 %s %s 추가" % (PATH, RID), reply["body"])
        self.assertEqual(len(self.mention_calls()), 2)  # the model ran again

    def test_recovery_after_review_before_reply(self):
        fid = self.reviewed()["issues"][0]["id"]
        m = self.mention("@ai-bot /철회 의도된 동작")
        self.script(mention=[{"out": answer("철회합니다."), "withdraw_all": True}])
        self.refuse_first_reply()
        self.assertEqual(self.run_phases(), 1)
        self.assertEqual(self.replies(), [])
        [review] = self.mention_reviews(m["key"])
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        [reply] = self.replies(m["key"])
        self.assertIn("이전 실행에서 반영했습니다. (%s)" % review["html_url"], reply["body"])
        self.assertIn("**반영:** 철회 " + fid, reply["body"])
        self.assertEqual(len(self.mention_calls()), 1)
        self.assertEqual(len(self.mention_reviews(m["key"])), 1)

    def test_receipt_wins_over_time_reply(self):
        self.reviewed()
        m = self.mention("@ai-bot /철회 의도된 동작")
        self.script(mention=[{"out": answer("철회합니다."), "withdraw_all": True}])
        self.refuse_first_reply()
        self.assertEqual(self.run_phases(), 1)
        self.assertEqual(self.run_phases("--set", "mention_deadline_s=10"), 0)
        [reply] = self.replies(m["key"])
        self.assertIn("이전 실행에서 반영했습니다.", reply["body"])
        self.assertNotIn("시간이 부족해", reply["body"])

    def test_recovery_guideline_removal(self):
        self.with_rule()
        self.reviewed()
        m = self.mention("@ai-bot /지침삭제 cudaMemcpy 지침은 이제 필요 없어")
        self.script(mention=[{"out": answer("삭제합니다.", guideline=guideline(
            "remove", target=RID))}])
        self.refuse_first_reply()
        self.assertEqual(self.run_phases(), 1)
        self.assertEqual(len(self.trailers(m["key"])), 1)
        # the model now proposes something else: the commit of the earlier run is reported
        self.script(mention=[{"out": answer("추가합니다.", guideline=guideline())}])
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        self.assertEqual(len(self.trailers(m["key"])), 1)
        [reply] = self.replies(m["key"])
        self.assertIn("지침 %s %s 삭제" % (PATH, RID), reply["body"])
        self.assertNotIn("반영 안 됨", reply["body"])

    def test_review_lost_response(self):
        self.reviewed()
        m = self.mention("@ai-bot /철회 의도된 동작")
        self.script(mention=[{"out": answer("철회합니다."), "withdraw_all": True}])
        self.gh.drop_response_once("/reviews")
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        self.assertEqual(len(self.mention_reviews(m["key"])), 1)
        self.assertEqual(len(self.replies(m["key"])), 1)

    def test_reply_lost_response(self):
        self.reviewed()
        m = self.mention("@ai-bot 이건 왜 문제야?")
        self.gh.drop_response_once("/comments")
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        self.assertEqual(len(self.replies(m["key"])), 1)

    def thread_mention(self):
        self.reviewed()
        root = next(c for c in self.gh.review_comments if c["user"]["login"] == "ai-bot")
        r = self.gh.add_review_comment("@ai-bot /철회 의도된 동작", in_reply_to=root["id"])
        self.script(mention=[{"out": answer("철회합니다."), "withdraw_all": True}])
        return root, "r:%d" % r["id"]

    def test_thread_reply_in_the_thread(self):
        root, key = self.thread_mention()
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        [reply] = self.replies(key)
        self.assertEqual(reply["in_reply_to_id"], root["id"])
        self.assertFalse(reply["body"].startswith(">"))
        self.assertEqual(self.gh.posted[-1]["event"], "APPROVE")

    def test_thread_reply_refused_falls_back_to_issue_comment(self):
        root, key = self.thread_mention()
        self.gh.fail_next_post(403, "/replies")
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        [reply] = self.replies(key)
        self.assertIn(reply, self.gh.issue_comments)
        self.assertTrue(reply["body"].startswith("> @\u200bai-bot /철회 의도된 동작"))
        self.assertIn("스레드 답글 거부됨", self.stdout)


if __name__ == "__main__":
    unittest.main()
