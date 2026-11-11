import unittest

from review.korean import KOREAN_MIN_LETTERS, is_korean, merge_korean


def F(line=3, title="범위를 벗어난 읽기", detail="`p[1]`은 버퍼 밖을 읽는다.", **kw):
    f = {"file": "a.cu", "line": line, "severity": "major", "confidence": 90,
         "category": "memory", "title": title, "detail": detail}
    f.update(kw)
    return f


def OUT(*findings, summary="커널에서 경계 오류 1건을 찾았다.", prev=(), reason=None):
    return {"summary": summary, "findings": list(findings), "previous_findings": list(prev),
            "incomplete_reason": reason}


EN = OUT(F(title="Out-of-bounds read", detail="`p[1]` reads past the end of the buffer.",
           suggestion="Check the length before indexing."),
         summary="Found one out-of-bounds bug in the kernel.")


class IsKoreanTest(unittest.TestCase):
    def test_threshold_constant(self):
        self.assertEqual(KOREAN_MIN_LETTERS, 12)

    def test_korean_output(self):
        self.assertTrue(is_korean(OUT(F(suggestion="`len`을 먼저 확인한다."))))

    def test_fully_english_output_fails(self):
        self.assertFalse(is_korean(EN))

    def test_english_summary_without_findings_fails(self):
        self.assertFalse(is_korean(OUT(summary="No problems were found in this change.")))

    def test_korean_summary_with_english_detail_passes(self):
        out = OUT(F(detail="The pointer is read after it was freed."),
                  summary="해제된 포인터를 읽는 버그가 있다.")
        self.assertTrue(is_korean(out))

    def test_one_hangul_character_is_enough(self):
        self.assertTrue(is_korean(OUT(F(detail="The pointer is read after it was freed 해."),
                                      summary="Found one bug in the kernel.")))

    def test_hangul_only_in_code_fails(self):
        self.assertFalse(is_korean(OUT(F(detail="Reads past the end of the buffer `한글`.",
                                         title="Out-of-bounds read"),
                                       summary="Found one bug in the kernel.")))
        fenced = "Check the length first.\n```cpp\n// 한글 주석\nx = 1;\n```"
        self.assertFalse(is_korean(OUT(F(detail="Reads past the end.", title="Out of bounds",
                                         suggestion=fenced), summary="")))

    def test_unclosed_fence_does_not_hide_other_items(self):
        out = OUT(F(suggestion="```cpp\nx = 1;\n"), summary="커널에서 오류를 찾았다.")
        self.assertTrue(is_korean(out))

    def test_empty_and_short_prose_counts_as_korean(self):
        self.assertTrue(is_korean(OUT(summary="")))
        self.assertTrue(is_korean(OUT(summary="LGTM")))
        self.assertTrue(is_korean({"summary": "", "findings": [], "previous_findings": []}))

    def test_letters_are_counted_across_items(self):
        out = OUT(F(title="Bounds", detail="Off by one", suggestion=""), summary="Short one")
        self.assertFalse(is_korean(out))  # 8 + 9 + 8 letters in total

    def test_inline_code_is_ignored(self):
        code = "`std::vector<int> buffer_with_a_long_identifier_name`"
        self.assertTrue(is_korean(OUT(F(detail=code, title=""), summary="")))
        self.assertFalse(is_korean(OUT(F(detail="use after free of the buffer", title=""), summary="")))

    MIXED = (
        "`std::vector<int>::push_back`가 realloc을 일으키면 iterator invalidation이 발생한다.",
        "mutex lock 없이 shared_ptr use_count를 읽으면 data race가 된다.",
        "CUDA kernel의 __device__ function에서 host pointer를 dereference한다.",
        "nullptr check 없이 cudaMalloc 결과를 cudaMemcpy에 넘긴다.",
    )

    def test_identifier_heavy_korean_sentences_pass(self):
        for t in self.MIXED:
            with self.subTest(t=t):
                self.assertTrue(is_korean(OUT(F(detail=t), summary="")))

    def test_incomplete_reason_counts_as_prose(self):
        self.assertTrue(is_korean(OUT(F(title="", detail=""), summary="", reason="호출부를 확인하지 못했다.")))
        self.assertFalse(is_korean(OUT(summary="", reason="Tool budget was exhausted before callers.")))

    def test_previous_note_counts_as_prose(self):
        prev = [{"id": "f1", "status": "unclear", "note": "락을 잡았는지 알 수 없다."}]
        self.assertTrue(is_korean(OUT(summary="", prev=prev)))
        prev = [{"id": "f1", "status": "unclear", "note": "Cannot tell whether the lock is held."}]
        self.assertFalse(is_korean(OUT(summary="", prev=prev)))


class MergeKoreanTest(unittest.TestCase):
    def test_takes_korean_prose(self):
        ko = OUT(F(suggestion="인덱싱 전에 길이를 확인한다."))
        self.assertEqual(merge_korean(EN, ko), ko)

    def test_keeps_same_as(self):
        en = OUT(F(title="Out-of-bounds read", detail="`p[1]` reads past the buffer.",
                   same_as="i1"), summary="Found one bug.")
        ko = OUT(F())
        self.assertEqual(merge_korean(en, ko)["findings"][0]["same_as"], "i1")

    def test_keeps_original_non_prose_fields(self):
        ko = OUT(F(confidence=50, suggestion="길이를 확인한다."))
        self.assertEqual(merge_korean(EN, ko)["findings"][0]["confidence"], 90)

    def test_carries_incomplete_reason(self):
        en = OUT(F(), reason="Could not inspect the callers.")
        ko = OUT(F(), reason="호출부를 확인하지 못했다.")
        self.assertEqual(merge_korean(en, ko)["incomplete_reason"], "호출부를 확인하지 못했다.")

    def test_incomplete_reason_only_when_original_had_one(self):
        ko = OUT(F(suggestion="길이를 확인한다."), reason="새 사유")
        self.assertIsNone(merge_korean(EN, ko)["incomplete_reason"])

    def test_diff_reviewed_stays_the_original(self):
        en = dict(EN, diff_reviewed=False)
        ko = dict(OUT(F(suggestion="길이를 확인한다.")), diff_reviewed=True)
        self.assertIs(merge_korean(en, ko)["diff_reviewed"], False)

    def test_accepts_a_korean_answer_that_leaves_the_suggestion_in_english(self):
        merged = merge_korean(EN, OUT(F()))  # no suggestion: the English one stays
        self.assertIsNotNone(merged)
        self.assertEqual(merged["findings"][0]["suggestion"], "Check the length before indexing.")

    def test_rejects_changed_findings(self):
        self.assertIsNone(merge_korean(EN, OUT(F(line=4, suggestion="확인"))))
        self.assertIsNone(merge_korean(EN, OUT(F(severity="minor"))))
        self.assertIsNone(merge_korean(EN, OUT()))
        self.assertIsNone(merge_korean(EN, OUT(F(), F())))

    def test_rejects_changed_previous_status(self):
        en = OUT(prev=[{"id": "f1", "status": "open"}], summary="Still open.")
        self.assertIsNone(merge_korean(en, OUT(prev=[{"id": "f1", "status": "resolved"}])))

    def test_rejects_still_english_or_missing(self):
        self.assertIsNone(merge_korean(EN, None))
        self.assertIsNone(merge_korean(EN, EN))




class MentionKoreanTest(unittest.TestCase):
    EN = {"reply": "Checked. This issue can be withdrawn because the caller checks the length.",
          "verdicts": [{"id": "a1", "withdraw": True, "reason": "The caller checks the length"}],
          "new_issues": [], "guideline": None, "wants": "withdraw"}
    KO = dict(EN, reply="확인했습니다. 호출부에서 길이를 검사하므로 철회해도 됩니다.",
              verdicts=[{"id": "a1", "withdraw": True, "reason": "호출부가 길이를 검사한다"}])

    def test_detects(self):
        from review.korean import is_korean_mention
        self.assertFalse(is_korean_mention(self.EN))
        self.assertTrue(is_korean_mention(self.KO))

    def test_merge_takes_prose_and_keeps_the_rest(self):
        from review.korean import merge_korean_mention
        m = merge_korean_mention(self.EN, self.KO)
        self.assertEqual(m["reply"], self.KO["reply"])
        self.assertEqual(m["verdicts"], self.KO["verdicts"])
        self.assertEqual(m["wants"], "withdraw")

    def test_merge_rejects_a_changed_verdict_or_wants(self):
        from review.korean import merge_korean_mention
        flipped = dict(self.KO, verdicts=[dict(self.KO["verdicts"][0], withdraw=False)])
        self.assertIsNone(merge_korean_mention(self.EN, flipped))
        self.assertIsNone(merge_korean_mention(self.EN, dict(self.KO, wants=None)))
        self.assertIsNone(merge_korean_mention(self.EN, None))
        self.assertIsNone(merge_korean_mention(self.EN, self.EN))  # still English

    def test_merge_guideline_prose_only(self):
        from review.korean import merge_korean_mention
        g = {"op": "add", "path": "g.md", "applies": ["*.c"], "rule": "Check lengths", "reason": "why"}
        en, ko = dict(self.EN, guideline=g), dict(self.KO, guideline=dict(g, rule="길이를 검사한다"))
        self.assertEqual(merge_korean_mention(en, ko)["guideline"]["rule"], "길이를 검사한다")
        moved = dict(ko, guideline=dict(g, rule="길이를 검사한다", path="other.md"))
        self.assertIsNone(merge_korean_mention(en, moved))


if __name__ == "__main__":
    unittest.main()
