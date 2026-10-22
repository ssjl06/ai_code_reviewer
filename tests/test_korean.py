import unittest

from review.korean import KOREAN_MIN_RATIO, is_korean, merge_korean


def F(line=3, title="범위를 벗어난 읽기", detail="`p[1]`은 버퍼 밖을 읽는다.", **kw):
    f = {"file": "a.cu", "line": line, "severity": "major", "confidence": 90,
         "category": "memory", "title": title, "detail": detail}
    f.update(kw)
    return f


def OUT(*findings, summary="커널에서 경계 오류 1건을 찾았다.", prev=()):
    return {"summary": summary, "findings": list(findings), "previous_findings": list(prev),
            "incomplete_reason": None}


EN = OUT(F(title="Out-of-bounds read", detail="`p[1]` reads past the end of the buffer.",
           suggestion="Check the length before indexing."),
         summary="Found one out-of-bounds bug in the kernel.")


class IsKoreanTest(unittest.TestCase):
    def test_threshold_constant(self):
        self.assertEqual(KOREAN_MIN_RATIO, 0.3)

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
        self.assertFalse(is_korean(OUT(F(detail="use after free 해제", title=""), summary="")))

    def test_mixed_ratio(self):
        # 6 Hangul vs 14 ASCII letters -> exactly 30% counts as Korean
        self.assertTrue(is_korean(OUT(F(title="한국어입니다", detail="abcdefg hijklmn"),
                                      summary="")))
        self.assertFalse(is_korean(OUT(F(title="한국어입니", detail="abcdefg hijklmn"),
                                       summary="")))

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
