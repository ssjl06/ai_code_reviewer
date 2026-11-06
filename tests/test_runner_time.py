"""Time limits of the runner, simulated: a fake clock and a scripted `run_process`."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from review.claude_runner import chunk_limit, run_all, run_chunk
from review.config import load_config
from review.prompt import FALLBACK_PROMPT, KOREAN_PROMPT

KO = {"summary": "커널에서 범위를 벗어난 읽기 1건을 찾았다.", "findings": [],
      "previous_findings": [], "incomplete_reason": None, "diff_reviewed": True}
EN = dict(KO, summary="Found one out-of-bounds read in the kernel.")


class Clock:
    def __init__(self):
        self.now = 5000.0

    def monotonic(self):
        return self.now

    def time(self):
        return self.now


class SimBase(unittest.TestCase):
    """`self.script(text, sid)` answers each call with (seconds, output or None, session id)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.paths = {"repo": tmp.name, "work": os.path.join(tmp.name, "work"),
                      "agents_file": os.path.join(tmp.name, "agents.json")}
        self.env = {"PATH": os.environ["PATH"], "AI_REVIEW_LLM_BASE_URL": "http://x",
                    "AI_REVIEW_LLM_API_KEY": "k"}
        self.clock, self.calls = Clock(), []
        self.log = io.StringIO()
        for name, value in (("time", self.clock), ("run_process", self.run_process)):
            p = mock.patch("review.claude_runner." + name, value)
            p.start()
            self.addCleanup(p.stop)
        self.cfg = dict(load_config(), claude_bin="claude")

    def run_process(self, cmd, env, cwd, text, limit, logger, tee=None, info=None):
        sid = cmd[cmd.index("--resume") + 1] if "--resume" in cmd else None
        secs, out, new_sid = self.script(text, sid)
        self.calls.append({"text": text, "sid": sid, "limit": limit, "start": self.clock.now,
                           "stop_at": env.get("AI_REVIEW_STOP_AT")})
        cut = secs >= limit
        self.clock.now += min(secs, limit)
        if new_sid:
            logger.feed(json.dumps({"type": "system", "subtype": "init", "session_id": new_sid,
                                    "model": "m"}) + "\n")
        if out and not cut:
            logger.feed(json.dumps({"type": "result", "subtype": "success", "num_turns": 1,
                                    "is_error": False, "duration_ms": 1, "duration_api_ms": 1,
                                    "session_id": new_sid, "structured_output": out}) + "\n")
        info.update(rc=0, stderr="")
        return "timeout" if cut else "exited"

    def chunks(self, n):
        return [{"index": i, "files": ["f%d.c" % i], "diff": "d", "est_tokens": 1}
                for i in range(n)]

    def texts(self):
        return [c["text"] for c in self.calls]

    def review(self, n, left=18000, **cfg):
        prompts = ["P%d" % i for i in range(n)]
        with contextlib.redirect_stdout(self.log):
            return run_all(self.chunks(n), prompts, dict(self.cfg, **cfg), self.paths, self.env,
                           self.clock.now + left)


class AbsoluteEndTest(SimBase):
    def one(self, deadline_in=None, limit=685):
        deadline = None if deadline_in is None else self.clock.now + deadline_in
        t0 = self.clock.now
        with contextlib.redirect_stdout(self.log):
            r = run_chunk(self.chunks(1)[0], "P", 40, self.cfg, self.paths, self.env, limit,
                          deadline)
        return r, self.clock.now - t0

    def test_the_40_chunk_limit_is_685(self):
        self.assertEqual(chunk_limit(self.cfg, 40), 685)

    def test_simulated_685_run_and_590_conclusion(self):
        self.script = lambda text, sid: (685, None, "s") if text == "P" else (590, KO, "s")
        r, took = self.one()
        self.assertEqual((r["status"], r["error"], took), ("ok", "timeout", 1275))
        self.assertLessEqual(took, 685 + 600)

    def test_conclusion_is_cut_at_the_absolute_end(self):
        self.script = lambda text, sid: (685, None, "s") if text == "P" else (9999, KO, "s")
        r, took = self.one()
        self.assertEqual((r["status"], r["error"], took),
                         ("incomplete", "timeout → timeout", 1285))

    def test_conclusion_gets_what_remains(self):
        self.script = lambda text, sid: (400, None, "s") if text == "P" else (800, KO, "s")
        r, took = self.one()
        self.assertEqual((r["status"], took), ("ok", 1200))
        self.assertEqual(self.calls[1]["limit"], 885)

    def test_global_deadline_caps_the_end(self):
        self.script = lambda text, sid: (685, None, "s") if text == "P" else (590, KO, "s")
        r, took = self.one(deadline_in=800)
        self.assertEqual((r["status"], took), ("incomplete", 800))
        self.assertEqual(self.calls[1]["limit"], 115)

    def test_first_attempt_is_capped_by_the_deadline_too(self):
        self.script = lambda text, sid: (9999, None, "s")
        r, took = self.one(deadline_in=100)
        self.assertEqual((r["status"], r["error"], took), ("incomplete", "timeout → deadline", 100))
        self.assertEqual(len(self.calls), 1)


class TimeStopTest(SimBase):
    def test_40_chunks_at_concurrency_1(self):
        cfg = dict(self.cfg, concurrency=1)
        self.assertEqual(chunk_limit(cfg, 40), 300)
        self.script = lambda text, sid: (900, None, "s") if not sid else (590, KO, sid)
        t0 = self.clock.now
        rs = self.review(40, concurrency=1)
        first = [c for c in self.calls if c["sid"] is None]
        self.assertLess(len(first), 40)
        for c in first:
            self.assertGreater(float(c["stop_at"]), c["start"])
            self.assertLessEqual(c["limit"], 300)
            self.assertGreaterEqual(t0 + 18000 - c["start"], 60)
        self.assertEqual(float(first[0]["stop_at"]) - first[0]["start"], 225)
        self.assertTrue(all(c["start"] + c["limit"] <= t0 + 18000 for c in self.calls))
        self.assertEqual({r["status"] for r in rs[:len(first) - 1]}, {"ok"})
        self.assertEqual({(r["status"], r["error"]) for r in rs[len(first):]},
                         {("incomplete", "deadline")})

    def test_every_chunk_short_of_the_deadline_still_gets_a_time_stop(self):
        self.script = lambda text, sid: (0, KO, "s")
        for left in (60, 61, 100, 299.5):
            with self.subTest(left=left):
                self.calls.clear()
                self.review(1, left=left)
                c = self.calls[0]
                self.assertGreater(float(c["stop_at"]), c["start"])

    def test_chunk_starts_with_exactly_a_minute_left(self):
        self.script = lambda text, sid: (0, KO, "s")
        self.assertEqual(self.review(1, left=60)[0]["status"], "ok")
        self.assertEqual(self.review(1, left=59.9)[0]["error"], "deadline")


class KoreanPassTest(SimBase):
    def answers(self, table):
        """`table`: chunk prompt -> (seconds, output or None); resumes answered by `self.resume`."""
        def script(text, sid):
            if sid:
                return self.resume(text, sid)
            i = int(text[1:])
            return table[text] + ("sess-%d" % i,)
        self.script = script

    def resume(self, text, sid):
        return (50, KO, sid) if text == KOREAN_PROMPT else (50, EN, sid + "-resumed")

    def test_translation_does_not_delay_later_chunks(self):
        self.answers({"P0": (100, EN), "P1": (100, KO), "P2": (100, KO)})
        t0 = self.clock.now
        rs = self.review(3, concurrency=1)
        self.assertEqual(self.texts(), ["P0", "P1", "P2", KOREAN_PROMPT])
        self.assertEqual([c["start"] - t0 for c in self.calls], [0, 100, 200, 300])
        self.assertEqual(rs[0]["output"]["summary"], KO["summary"])

    def test_pass_covers_normal_and_fallback_chunks_on_the_answering_session(self):
        self.answers({"P0": (100, EN), "P1": (100, None), "P2": (100, KO)})
        rs = self.review(3, concurrency=1)
        self.assertEqual(self.texts(), ["P0", "P1", FALLBACK_PROMPT, "P2", KOREAN_PROMPT,
                                        KOREAN_PROMPT])
        self.assertEqual([c["sid"] for c in self.calls[4:]], ["sess-0", "sess-1-resumed"])
        self.assertEqual([r["status"] for r in rs], ["ok", "ok", "ok"])
        self.assertEqual([r["output"]["summary"] for r in rs], [KO["summary"]] * 3)

    def test_each_reask_is_limited_by_the_grace_after_the_deadline(self):
        self.answers({"P0": (100, EN)})
        self.review(1, left=150)
        self.assertEqual(self.calls[1]["limit"], 150 - 100 + 300)
        self.calls.clear()
        self.answers({"P0": (100, EN)})
        self.review(1, left=3600)
        self.assertEqual(self.calls[1]["limit"], self.cfg["fallback_timeout_s"])

    def test_pass_past_the_end_is_skipped_and_logged(self):
        self.answers({"P0": (100, EN)})
        with mock.patch("review.claude_runner.KOREAN_GRACE_S", -18000):
            rs = self.review(1)
        self.assertEqual(self.texts(), ["P0"])
        self.assertEqual((rs[0]["status"], rs[0]["output"]), ("ok", EN))
        self.assertIn("[c1] 시간 부족 — 한국어 재요청 생략, 원문 유지", self.log.getvalue())

    def test_reasks_stop_at_the_end_of_the_grace(self):
        self.answers({"P0": (100, EN), "P1": (100, EN)})
        self.resume = lambda text, sid: (9999, None, sid)
        rs = self.review(2, left=300, concurrency=1)
        self.assertEqual(self.texts(), ["P0", "P1", KOREAN_PROMPT])
        self.assertEqual([r["output"] for r in rs], [EN, EN])
        self.assertEqual(self.calls[2]["limit"], 400)
        log = self.log.getvalue()
        self.assertIn("[c1] 한국어 재요청 실패 — 원문 유지", log)
        self.assertIn("[c2] 시간 부족 — 한국어 재요청 생략, 원문 유지", log)

    def test_chunk_without_session_keeps_output_and_logs_it(self):
        self.script = lambda text, sid: (10, EN, None)
        rs = self.review(1)
        self.assertEqual((rs[0]["status"], rs[0]["output"]), ("ok", EN))
        self.assertEqual(len(self.calls), 1)
        self.assertIn("[c1] 세션 없음 — 한국어 재요청 생략, 원문 유지", self.log.getvalue())

    def test_reask_failure_never_changes_the_status(self):
        partial = dict(EN, diff_reviewed=False)
        for status, out in (("ok", EN), ("partial", partial)):
            with self.subTest(status=status):
                self.calls.clear()
                self.answers({"P0": (100, out)})
                self.resume = lambda text, sid: (_ for _ in ()).throw(OSError("gone"))
                rs = self.review(1)
                self.assertEqual((rs[0]["status"], rs[0]["output"]["summary"]),
                                 (status, EN["summary"]))
                self.assertIn("한국어 재요청 실패 — 원문 유지", self.log.getvalue())

    def test_korean_answer_keeps_partial(self):
        self.answers({"P0": (100, dict(EN, diff_reviewed=False))})
        self.resume = lambda text, sid: (50, dict(KO, diff_reviewed=True), sid)
        rs = self.review(1)
        self.assertEqual((rs[0]["status"], rs[0]["error"]), ("partial", "diff not reviewed"))
        self.assertEqual(rs[0]["output"]["summary"], KO["summary"])
        self.assertIs(rs[0]["output"]["diff_reviewed"], False)

    def test_english_suggestion_is_replaced_by_the_reask(self):
        finding = {"file": "a.c", "line": 1, "severity": "major", "confidence": 90,
                   "category": "bug", "title": "범위를 벗어난 읽기",
                   "detail": "버퍼 끝을 넘어 읽는다."}
        en = dict(EN, findings=[dict(finding, title="Out-of-bounds read",
                                     detail="It reads past the end of the buffer.",
                                     suggestion="Check the length before indexing.")])
        ko = dict(KO, findings=[dict(finding, title="Out-of-bounds read",
                                     detail="It reads past the end of the buffer.",
                                     suggestion="인덱싱 전에 길이를 확인한다.")])
        self.answers({"P0": (100, en)})
        self.resume = lambda text, sid: (50, ko, sid)
        rs = self.review(1)
        self.assertEqual(self.texts(), ["P0", KOREAN_PROMPT])
        self.assertEqual(rs[0]["output"]["findings"][0]["suggestion"], "인덱싱 전에 길이를 확인한다.")

    def test_wall_time_includes_the_reask(self):
        self.answers({"P0": (100, EN)})
        self.assertEqual(self.review(1)[0]["stats"]["wall_s"], 150)


if __name__ == "__main__":
    unittest.main()
