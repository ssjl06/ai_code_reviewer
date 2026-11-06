import unittest

from review.korean import KOREAN_MIN_HANGUL, _item_korean, _suggestion_prose, is_korean, merge_korean


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
        self.assertEqual(KOREAN_MIN_HANGUL, 2)

    def test_korean_output(self):
        self.assertTrue(is_korean(OUT(F(suggestion="`len`을 먼저 확인한다."))))

    def test_english_output(self):
        self.assertFalse(is_korean(EN))

    def test_empty_and_short_prose_counts_as_korean(self):
        self.assertTrue(is_korean(OUT(summary="")))
        self.assertTrue(is_korean(OUT(summary="LGTM")))
        self.assertTrue(is_korean({"summary": "", "findings": [], "previous_findings": []}))

    def test_inline_code_is_ignored(self):
        code = "`std::vector<int> buffer_with_a_long_identifier_name`"
        self.assertTrue(is_korean(OUT(F(detail=code + " 해제 후 사용"), summary="")))
        self.assertFalse(is_korean(OUT(F(detail="use after free 해", title=""), summary="")))

    def test_mixed_text_needs_two_hangul(self):
        self.assertTrue(is_korean(OUT(F(detail="한국어 abcdefg hijklmn"), summary="")))
        self.assertFalse(is_korean(OUT(F(detail="한 abcdefg hijklmn"), summary="")))

    MIXED = (
        "`std::vector<int>::push_back`가 realloc을 일으키면 iterator invalidation이 발생한다.",
        "mutex lock 없이 shared_ptr use_count를 읽으면 data race가 된다.",
        "CUDA kernel의 __device__ function에서 host pointer를 dereference한다.",
        "nullptr check 없이 cudaMalloc 결과를 cudaMemcpy에 넘긴다.",
    )

    def test_identifier_heavy_korean_sentences_pass(self):
        for t in self.MIXED:
            with self.subTest(t=t):
                self.assertTrue(_item_korean(t))
                self.assertTrue(is_korean(OUT(F(detail=t), summary="")))

    def test_fully_english_sentence_still_fails(self):
        t = "The pointer is read after it was freed."
        self.assertFalse(_item_korean(t))
        self.assertFalse(is_korean(OUT(F(detail=t), summary="")))

    def test_one_english_detail_in_korean_output_fails(self):
        out = OUT(F(detail="The pointer is read after it was freed."),
                  summary="해제된 포인터를 읽는 버그가 있다.")
        self.assertFalse(is_korean(out))

    def test_each_item_is_judged_alone(self):
        ko = [F(detail="`p[1]`은 길이 1 버퍼의 끝을 넘어서 읽기 때문에 범위 오류가 된다.")] * 5
        for bad in (dict(title="Out-of-bounds read"),
                    dict(detail="`p[1]` reads past the end of the buffer.")):
            with self.subTest(bad=bad):
                self.assertFalse(is_korean(OUT(*ko, F(**bad))))
        self.assertTrue(is_korean(OUT(*ko, F(title="Fix bounds"))))  # too short to judge

    def test_english_incomplete_reason_fails(self):
        self.assertFalse(is_korean(OUT(F(), reason="Tool budget was exhausted before callers.")))
        self.assertTrue(is_korean(OUT(F(), reason="호출부를 확인하지 못했다.")))

    def test_code_suggestion_is_not_judged(self):
        code = "if (index < buffer_length) { value = buffer[index]; }"
        self.assertTrue(is_korean(OUT(F(suggestion=code))))

    def test_prose_with_four_plain_words_is_kept(self):
        for text in ("if the buffer is empty, skip the copy and return early.",
                     "return an error when the size is zero instead of crashing.",
                     "Use a lock around this block;",
                     "Check the length (in bytes) before indexing the buffer.",
                     "Check the length; then index the buffer."):
            self.assertEqual(_suggestion_prose(text), text)
            self.assertFalse(is_korean(OUT(F(suggestion=text))), text)

    def test_code_lines_are_still_removed(self):
        for text in ("return foo(bar);", "for (int i = 0; i < n; ++i) {", "auto x = compute();",
                     "#include <vector>", "}"):
            self.assertEqual(_suggestion_prose(text), "", text)

    def test_english_prose_suggestion_fails(self):
        self.assertFalse(is_korean(OUT(F(suggestion="Check the length before indexing the buffer."))))

    def test_prose_with_code_characters_is_judged(self):
        for text in ("Check the length (in bytes) before indexing the buffer.",
                     "Use a lock here; the map is shared between threads.",
                     "Set the limit = 5 for this path."):
            with self.subTest(text=text):
                self.assertFalse(is_korean(OUT(F(suggestion=text))))

    def test_code_lines_as_a_whole_are_not_judged(self):
        for code in ("std::lock_guard<std::mutex> lock(mu);", "x = compute(y)", "foo(a, b)",
                     "total += compute_the_total_value(items)", "#include <mutex_and_condition>",
                     "// guard the shared map with a mutex", "#!/usr/bin/env python_interpreter",
                     "return checked_lookup_of_the_buffer_value", "while (retry_budget_remaining) do_it",
                     "import concurrent_futures_thread_pool", "const auto& entry_in_the_map = m[k]",
                     "struct Entry { int key_of_the_entry; int value_of_the_entry; }"):
            with self.subTest(code=code):
                self.assertTrue(is_korean(OUT(F(suggestion=code))))

    def test_code_only_suggestion_passes(self):
        for code in ("```cpp\nif (index < length) {\n  value = buffer[index];\n}\n```",
                     "```\nreturn checked_lookup_of_the_buffer_value\n```",
                     "~~~\nreturn checked_lookup_of_the_buffer_value\n~~~",
                     "```python\nwith_open_file_and_a_long_identifier\n",  # never closed
                     "if (index < buffer_length) {\n  value = buffer[index];\n}\n",
                     "`checked_lookup_of_the_buffer_value`"):
            with self.subTest(code=code):
                self.assertTrue(is_korean(OUT(F(suggestion=code))))

    def test_korean_sentence_with_code_block_passes(self):
        code = "```cpp\nreturn checked_lookup_of_the_buffer_value\n```"
        self.assertTrue(is_korean(OUT(F(suggestion="인덱싱 전에 길이를 확인한다.\n" + code))))

    def test_english_sentence_with_code_block_fails(self):
        code = "```cpp\nvalue = buffer[index];\n```"
        self.assertFalse(is_korean(OUT(F(suggestion="Check the length first, then index it.\n" + code))))

    def test_inline_code_goes_before_the_code_line_check(self):
        s = "Replace `lookup(index)` with the checked variant of this helper."
        self.assertFalse(is_korean(OUT(F(suggestion=s))))
        self.assertTrue(is_korean(OUT(F(suggestion="`lookup(index) = checked_value`로 바꾼다."))))

    def test_suggestion_uses_the_same_thresholds(self):
        self.assertTrue(is_korean(OUT(F(suggestion="Fix bounds"))))  # too short to judge
        self.assertTrue(is_korean(OUT(F(suggestion="한국어 abcdefg hijklmn"))))
        self.assertFalse(is_korean(OUT(F(suggestion="한 abcdefg hijklmn"))))

    def test_previous_note_checked(self):
        prev = [{"id": "f1", "status": "unclear", "note": "Cannot tell whether the lock is held."}]
        self.assertFalse(is_korean(OUT(summary="", prev=prev)))
        prev = [{"id": "f1", "status": "unclear", "note": "락을 잡았는지 알 수 없다."}]
        self.assertTrue(is_korean(OUT(summary="", prev=prev)))


class MergeKoreanTest(unittest.TestCase):
    def test_takes_korean_prose(self):
        ko = OUT(F(suggestion="인덱싱 전에 길이를 확인한다."))
        self.assertEqual(merge_korean(EN, ko), ko)

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

    def test_rejects_an_answer_that_leaves_the_suggestion_in_english(self):
        self.assertIsNone(merge_korean(EN, OUT(F())))  # no suggestion: the English one would stay

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


if __name__ == "__main__":
    unittest.main()
