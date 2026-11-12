"""handle and publish units: the cause shown when a session gave no output; the head wait."""
import unittest
from types import SimpleNamespace
from unittest import mock

from review.mention import handle, publish


class NoOutputCauseTest(unittest.TestCase):
    def test_runner_codes_get_the_review_body_labels(self):
        self.assertEqual(handle.cause_text("timeout"), "시간 초과")
        self.assertEqual(handle.cause_text("max_turns → no structured output"),
                         "턴 한도 도달 → 결론 단계 실패 (구조화 출력 없음)")

    def test_raw_runner_text_is_not_shown(self):
        for raw in ("Traceback: secret path /home/x", "no output (exit 1)", None, ""):
            self.assertEqual(handle.cause_text(raw), "출력 없음", raw)


class WaitHeadTest(unittest.TestCase):
    def test_reads_and_delay_come_from_the_config(self):
        reads = []

        def get_pr():
            reads.append(1)
            return {"head": {"sha": "old"}}
        ph = SimpleNamespace(head="new", pushed={"new"}, gh=SimpleNamespace(get_pr=get_pr),
                             cfg={"mention_head_tries": 4, "mention_head_delay_s": 0.25})
        with mock.patch.object(publish.time, "sleep") as sleep:
            pr = publish.wait_head(ph, {"head": {"sha": "old"}})
        self.assertEqual(pr["head"]["sha"], "old")
        self.assertEqual(len(reads), 3)  # 4 reads in all, the caller's first included
        self.assertEqual([c.args for c in sleep.call_args_list], [(0.25,)] * 3)


if __name__ == "__main__":
    unittest.main()
