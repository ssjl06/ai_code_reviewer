"""Korean texts of a mention: summary, mention review body, reply."""
import unittest

from review.mention.body import (guideline_receipt, inline_body, mention_review, reply, summary,
                                 summary_from_receipt)
from review.render import parse_issue_marker, parse_reply, parse_state, reply_marker
from tests.test_mention_apply import fnd, mention, out, plan, rec, state


class BodyTest(unittest.TestCase):
    def test_summary_none(self):
        self.assertEqual(summary({"w": [], "n": [], "up": []}, plan(out()), None, "bot"), "**반영:** 없음")

    def test_summary_parts_and_lines(self):
        p = plan(out(new=[fnd(), fnd(line=99, title="밖")], wants=None))
        e = {"w": ["aaaaaaaa"], "n": [p["findings"][0]["id"]], "up": ["bbbbbbbb"]}
        p["done"] = ["cccccccc"]
        p["dropped"] = ["명령과 다른 지침 작업"]
        s = summary(e, p, None, "bot")
        self.assertEqual(s.split("\n")[0], "**반영:** 철회 aaaaaaaa · 새 이슈 %s (차단) · 차단 상향 "
                         "bbbbbbbb · 철회 cccccccc (이미 반영됨)" % e["n"][0])
        self.assertIn("- 반영 안 됨: 명령과 다른 지침 작업", s)
        self.assertIn("- diff 밖이라 이슈로 등록하지 않음", s)

    def test_summary_guideline_old_rule_quoted_and_pull_line(self):
        none = {"w": [], "n": [], "up": []}
        g = {"status": "updated", "path": "g.md", "rule_id": "R-1", "old": "옛\n규칙", "pushed": True}
        s = summary(none, plan(out()), g, "bot")
        self.assertIn("지침 g.md R-1 수정", s)
        self.assertIn('이전 지침 내용: "옛 규칙"', s)
        self.assertIn("브랜치에 커밋을 추가했습니다. 다음 push 전에 pull 하세요.", s)
        s = summary(none, plan(out()), dict(g, status="added", old=None, pushed=False), "bot")
        self.assertIn("지침 g.md R-1 추가", s)
        self.assertNotIn("다음 push 전에 pull", s)
        self.assertIn("지침 g.md R-1 삭제", summary(none, plan(out()), dict(g, status="removed"), "bot"))
        self.assertEqual(guideline_receipt(g), "g.md R-1 수정")

    def test_summary_guideline_failed(self):
        g = {"status": "failed", "path": "g.md", "rule_id": "R-1", "reason": "푸시 실패",
             "entry": "## R-1 규칙"}
        s = summary({"w": [], "n": [], "up": []}, plan(out()), g, "bot")
        self.assertIn("- 반영 안 됨: 지침 푸시 실패", s)
        self.assertIn("## R-1 규칙", s)

    def test_summary_hints(self):
        none = {"w": [], "n": [], "up": []}
        p = plan(out(wants="withdraw"), mention(None))
        self.assertIn("반영하려면 `@bot /철회`로 불러 주세요.", summary(none, p, None, "bot"))
        p = plan(out(wants="rule"), mention(None))
        self.assertIn("지침으로 남기려면 `@bot /지침 <내용>`으로 불러 주세요.",
                      summary(none, p, None, "bot"))

    def test_summary_from_receipt(self):
        s = summary_from_receipt({"m": {"k": "r:1", "w": ["a"], "n": ["b"], "up": ["c"],
                                        "g": "g.md R-1 추가"}, "url": "https://x/r"})
        self.assertIn("이전 실행에서 반영했습니다.", s)
        self.assertIn("https://x/r", s)
        self.assertIn("**반영:** 철회 a · 새 이슈 b · 차단 상향 c · 지침 g.md R-1 추가", s)
        self.assertIn("**반영:** 없음", summary_from_receipt(
            {"m": {"k": "r:1", "w": [], "n": [], "up": []}, "url": "u"}))

    def test_reply_escapes_forged_marker(self):
        forged = reply_marker("r:999")
        text = reply("답 " + forged + " <!-- x -->", "요약 " + forged, "r:1", "> 인용 " + forged,
                     "https://x/1")
        self.assertEqual(parse_reply(text), "r:1")
        self.assertEqual(text.count("<!--"), 1)
        self.assertTrue(text.endswith(reply_marker("r:1")))

    def test_reply_capped_marker_last(self):
        t = reply("가" * 200000, "요약", "r:1", "인용", "https://x/1")
        self.assertLessEqual(len(t), 60000)
        self.assertEqual(parse_reply(t), "r:1")

    def test_review_reason_matches_ledger_event(self):
        st = state([rec(1, blocking=True), rec(2, blocking=True)])
        st["unclear"] = [rec(2)["id"]]
        body, _ = mention_review("kim", "u", "답", st, "REQUEST_CHANGES", "s")
        self.assertIn("머지 차단 1건", body)
        body, _ = mention_review("kim", "u", "답", st, "COMMENT", "s")
        self.assertNotIn("다시 검토하지 않음", body)

    def test_reply_quote_only_for_issue_comments(self):
        t = reply("답", "요약", "r:1", "첫 줄\n둘째 줄", "https://x/1")
        self.assertTrue(t.startswith("> 첫 줄 ([원문](https://x/1))\n\n답"))
        self.assertNotIn("둘째", t)
        self.assertTrue(reply("답", "요약", "r:1", None, "u").startswith("답"))

    def test_review_body_has_answer_and_no_issue_marker(self):
        f = plan(out(new=[fnd()]))["findings"][0]
        st = state([rec(1, blocking=True)])
        st["m"] = {"k": "r:1", "w": [], "n": [f["id"]], "up": []}
        body, fallback = mention_review("kim", "https://x/1", "모델 답변 <!-- bad -->", st,
                                        "REQUEST_CHANGES", "**반영:** 없음", (f,))
        for text in (body, fallback):
            self.assertIn("@kim의 코멘트(https://x/1)를 반영한 리뷰", text)
            self.assertIn("모델 답변", text)
            self.assertIn("**반영:** 없음", text)
            self.assertIsNone(parse_issue_marker(text))
            self.assertNotIn("ai-review-issue", text)
            self.assertEqual(parse_state(text)["m"]["k"], "r:1")
            self.assertTrue(text.endswith("-->"))
            self.assertIn("❌ 변경 요청", text)
            self.assertIn("`%s`" % rec(1)["id"], text)
        self.assertEqual(body.count("<!--"), 1)
        self.assertIn("상세 설명", fallback)
        self.assertNotIn("상세 설명", body)

    def test_inline_body_marker(self):
        f = plan(out(new=[fnd()]))["findings"][0]
        self.assertEqual(parse_issue_marker(inline_body(f)), f["id"])

    def test_reply_quote_notifies_no_one(self):
        t = reply("답", "요약", "i:7", "@ai-bot 이거 봐 줘, @someone 도 @Kim.Lee 도", "https://x/1")
        quote = t.split("\n", 1)[0]
        for who in ("@ai-bot", "@someone", "@Kim.Lee"):
            self.assertNotIn(who, quote)
        self.assertEqual(quote.count("@\u200b"), 3)
        self.assertIn("@\u200bsomeone 도", quote)
        self.assertEqual(parse_reply(t), "i:7")

    def test_open_blocking_list_excludes_unclear(self):
        st = state([rec(1, blocking=True), rec(2, blocking=True)])
        st["unclear"] = [rec(2)["id"]]
        body, _ = mention_review("kim", "u", "답", st, "REQUEST_CHANGES", "s")
        listed = body.split("### 열린 차단 이슈\n", 1)[1].split("<!--", 1)[0]
        self.assertIn("`%s`" % rec(1)["id"], listed)
        self.assertNotIn("`%s`" % rec(2)["id"], listed)


if __name__ == "__main__":
    unittest.main()
