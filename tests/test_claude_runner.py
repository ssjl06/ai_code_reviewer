import contextlib
import io
import json
import os
import tempfile
import time
import unittest

from unittest import mock

from review.claude_runner import (TIME_RESERVE_S, _cause, _denial_summary, chunk_limit, extract_output, run_all,
                                  run_chunk, run_process)
from review.config import load_config
from review.hooks.guard import DENY_STOP_FILE, STOP_FILE, TIME_STOP_FILE
from review.stream_log import StreamLogger
from review.prompt import FALLBACK_PROMPT, KOREAN_PROMPT
from tests.fakes import make_wrapper


LONG = TIME_RESERVE_S + 60  # the time stop stays out of the way


def stop_now():
    """Put AI_REVIEW_STOP_AT at the start, so the guard's first check fires."""
    return mock.patch("review.claude_runner._reserve", lambda limit: limit)


def quiet():
    return contextlib.redirect_stdout(io.StringIO())


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = {"repo": self.tmp.name, "work": os.path.join(self.tmp.name, "work"),
                      "agents_file": os.path.join(self.tmp.name, "agents.json")}
        self.env = {"PATH": os.environ["PATH"], "HOME": self.tmp.name,
                    "AI_REVIEW_LLM_BASE_URL": "http://x", "AI_REVIEW_LLM_API_KEY": "k"}
        self.chunk = {"index": 0, "files": ["a.c"], "diff": "d", "est_tokens": 100}

    def cfg(self, scn, resume="ok"):
        d = tempfile.mkdtemp(dir=self.tmp.name)
        binp, self.log = make_wrapper(d, scn, resume)
        return {"claude_bin": binp, "chunk_timeout_s": 2, "fallback_timeout_s": 2, "max_turns": 10,
                "llm_timeout_ms": 1000, "bash_timeout_ms": 1000, "explore_budget": 6, "deny_limit": 5,
                "model": "m", "concurrency": 1, "deadline_s": 1000}

    def run_scn(self, scn, resume="ok", deadline=None, out=None, timeout_s=None):
        with contextlib.redirect_stdout(out or io.StringIO()):
            return run_chunk(self.chunk, "PROMPT", 1, self.cfg(scn, resume), self.paths, self.env,
                             timeout_s, deadline)

    def run_review(self, scn, resume="ok", out=None, limit=LONG):
        """One chunk through `run_all`, which holds the Korean pass."""
        with contextlib.redirect_stdout(out or io.StringIO()), \
                mock.patch("review.claude_runner.chunk_limit", return_value=limit):
            return run_all([self.chunk], ["PROMPT"], self.cfg(scn, resume), self.paths, self.env,
                           time.monotonic() + 1000)[0]

    def calls(self):
        with open(self.log, encoding="utf-8") as f:
            return [json.loads(x) for x in f]

    def test_incomplete_reason_is_only_a_note(self):
        r = self.run_scn("noted")
        self.assertEqual((r["status"], r["error"]), ("ok", None))
        self.assertEqual(r["output"]["incomplete_reason"], "탐색 예산 부족")
        self.assertEqual(len(r["output"]["findings"]), 1)
        self.assertEqual(len(self.calls()), 1)

    def test_note_with_diff_reviewed_is_ok(self):
        r = self.run_scn("noted")
        self.assertTrue(r["output"]["diff_reviewed"])

    def test_diff_not_reviewed_is_partial(self):
        r = self.run_scn("unreviewed")
        self.assertEqual((r["status"], r["error"]), ("partial", "diff not reviewed"))
        self.assertEqual(len(r["output"]["findings"]), 1)
        self.assertEqual(len(self.calls()), 1)

    def test_fallback_answer_decides_partial_too(self):
        r = self.run_scn("max_turns", "unreviewed")
        self.assertEqual((r["status"], r["error"]), ("partial", "diff not reviewed"))

    def test_report_logs_partial(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            run_all([self.chunk], ["p"], self.cfg("unreviewed"), self.paths, self.env,
                    time.monotonic() + 120)
        self.assertIn("[c1] ✖ partial  diff not reviewed", out.getvalue())

    def test_ok(self):
        r = self.run_scn("ok")
        self.assertEqual((r["status"], r["session_id"]), ("ok", "sess-fake"))
        self.assertEqual(len(self.calls()), 1)
        self.assertTrue(os.path.isdir(os.path.join(self.paths["work"], "chunk-1", "state")))

    def test_max_turns_resumes(self):
        r = self.run_scn("max_turns")
        self.assertEqual(r["status"], "ok")
        c = self.calls()
        self.assertIn("--resume", c[1]["argv"])
        self.assertEqual(c[1]["stdin"], FALLBACK_PROMPT)
        with open(os.path.join(self.paths["work"], "chunk-1", "stream.jsonl")) as f:
            self.assertEqual(sum(1 for x in f if '"init"' in x), 2)

    def test_english_output_reasked_in_korean(self):
        log = io.StringIO()
        r = self.run_review("english", "korean", out=log)
        self.assertEqual((r["status"], r["error"]), ("ok", None))
        f = r["output"]["findings"][0]
        self.assertEqual((f["title"], f["line"]), ("범위를 벗어난 읽기", 2))
        self.assertTrue(r["output"]["summary"].startswith("커널에서"))
        c = self.calls()
        self.assertEqual(len(c), 2)
        self.assertEqual(c[1]["stdin"], KOREAN_PROMPT)
        self.assertEqual(c[1]["argv"][c[1]["argv"].index("--resume") + 1], "sess-fake")
        self.assertIn("한국어로 다시 받음", log.getvalue())

    def test_korean_reask_changing_findings_keeps_original(self):
        log = io.StringIO()
        r = self.run_review("english", "korean_moved", out=log)
        self.assertEqual((r["status"], r["error"]), ("ok", None))
        f = r["output"]["findings"][0]
        self.assertEqual((f["title"], f["line"]), ("Out-of-bounds read", 2))
        self.assertEqual(len(self.calls()), 2)
        self.assertIn("한국어 재요청 실패 — 원문 유지", log.getvalue())

    def test_korean_reask_invalid_keeps_original(self):
        for resume in ("broken", "english", "crash", "max_turns"):
            with self.subTest(resume=resume):
                log = io.StringIO()
                r = self.run_review("english", resume, out=log)
                self.assertEqual((r["status"], r["error"]), ("ok", None))
                self.assertEqual(r["output"]["findings"][0]["title"], "Out-of-bounds read")
                self.assertIn("한국어 재요청 실패 — 원문 유지", log.getvalue())

    def test_korean_output_not_reasked(self):
        r = self.run_review("blocking")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(len(self.calls()), 1)

    def test_run_chunk_leaves_translation_to_the_pass(self):
        r = self.run_scn("english", "korean")
        self.assertEqual(r["output"]["findings"][0]["title"], "Out-of-bounds read")
        self.assertEqual(len(self.calls()), 1)

    def test_korean_reask_shares_the_chunk_session_dirs(self):
        self.run_review("english", "korean")
        first, again = self.calls()
        self.assertEqual(first["config_dir"], again["config_dir"])
        self.assertTrue(first["config_dir"].endswith(os.path.join("chunk-1", "config")))
        self.assertIsNone(again["stop_at"])

    def test_max_turns_cause_after_failed_fallback(self):
        r = self.run_scn("max_turns", "broken")
        self.assertEqual((r["status"], r["error"]), ("incomplete", "max_turns → no structured output"))

    def test_budget_stop_resumes_to_conclusion(self):
        r = self.run_scn("budget_stop", timeout_s=LONG)
        self.assertEqual((r["status"], r["error"]), ("ok", "budget"))
        self.assertTrue(os.path.exists(os.path.join(self.paths["work"], "chunk-1", "state",
                                                    STOP_FILE)))
        c = self.calls()
        self.assertEqual(len(c), 2)
        a = c[1]["argv"]
        self.assertEqual((a[a.index("--resume") + 1], c[1]["stdin"]), ("sess-fake", FALLBACK_PROMPT))
        self.assertEqual((a[a.index("--max-turns") + 1], a[a.index("--tools") + 1]), ("4", ""))

    def test_fallback_output_reasked_in_korean(self):
        for first, cause in (("max_turns", "max_turns"), ("budget_stop", "budget")):
            with self.subTest(first=first):
                r = self.run_review(first, "english_then_korean")
                self.assertEqual((r["status"], r["error"]), ("ok", cause))
                self.assertEqual(r["output"]["findings"][0]["title"], "범위를 벗어난 읽기")
                self.assertEqual([c["stdin"] for c in self.calls()[1:]],
                                 [FALLBACK_PROMPT, KOREAN_PROMPT])

    def test_budget_stop_fallback_fails(self):
        r = self.run_scn("budget_stop", "broken", timeout_s=LONG)
        self.assertEqual((r["status"], r["error"]), ("incomplete", "budget → no structured output"))

    def test_time_stop_resumes_to_conclusion(self):
        t = time.time()
        with stop_now():
            r = self.run_scn("time_stop", timeout_s=TIME_RESERVE_S)
        self.assertEqual((r["status"], r["error"]), ("ok", "time"))
        state = os.path.join(self.paths["work"], "chunk-1", "state")
        self.assertTrue(os.path.exists(os.path.join(state, TIME_STOP_FILE)))
        self.assertFalse(os.path.exists(os.path.join(state, STOP_FILE)))
        c = self.calls()
        self.assertEqual([x["stdin"] for x in c], ["PROMPT", FALLBACK_PROMPT])
        self.assertAlmostEqual(float(c[0]["stop_at"]), t, delta=5)

    def test_resume_runs_without_time_stop(self):
        with stop_now():
            self.run_review("time_stop", "english_then_korean", limit=TIME_RESERVE_S)
        c = self.calls()
        self.assertEqual(len(c), 3)
        self.assertEqual([x["stop_at"] for x in c[1:]], [None, None])

    def test_stop_at_leaves_the_reserve(self):
        for limit, stop in ((2000, 1700), (1000, 750), (685, 514), (300, 225), (120, 90), (60, 45)):
            with self.subTest(limit=limit):
                t = time.time()
                self.run_scn("ok", timeout_s=limit)
                self.assertAlmostEqual(float(self.calls()[0]["stop_at"]), t + stop, delta=5)

    def test_time_stop_does_not_fire_at_the_start(self):
        r = self.run_scn("time_stop", timeout_s=TIME_RESERVE_S)
        self.assertEqual((r["status"], r["error"]), ("ok", "budget"))
        state = os.path.join(self.paths["work"], "chunk-1", "state")
        self.assertFalse(os.path.exists(os.path.join(state, TIME_STOP_FILE)))

    def test_time_stop_fallback_fails(self):
        with stop_now():
            r = self.run_scn("time_stop", "broken", timeout_s=TIME_RESERVE_S)
        self.assertEqual((r["status"], r["error"]), ("incomplete", "time → no structured output"))

    def test_chunk_limit_examples(self):
        cfg = load_config()
        self.assertEqual([chunk_limit(cfg, n) for n in (1, 3, 23, 40)], [3600, 3600, 1650, 685])

    def test_chunk_limit_floor_and_no_chunks(self):
        cfg = {"chunk_timeout_s": 3600, "fallback_timeout_s": 600, "deadline_s": 1000,
               "concurrency": 1}
        self.assertEqual(chunk_limit(cfg, 40), 300)
        self.assertEqual(chunk_limit(cfg, 0), 400)

    def test_run_all_uses_chunk_limit_capped_by_time_left(self):
        cfg = load_config()
        for left, want in ((100000, 1650), (100, 100), (61, 61)):
            with self.subTest(left=left), mock.patch("review.claude_runner.run_chunk") as rc:
                rc.return_value = {"index": 0, "status": "ok"}
                run_all([self.chunk] * 23, ["p"] * 23, cfg, self.paths, self.env,
                        time.monotonic() + left)
                self.assertAlmostEqual(rc.call_args_list[0].args[6], want, delta=2)

    def test_cause_codes(self):
        self.assertEqual(_cause("timeout", {}), "timeout")
        self.assertEqual(_cause("exited", {"result": {"subtype": "error_max_turns"}}), "max_turns")
        self.assertEqual(_cause("exited", {"result": {"result": '{"x": 1}'}}), "invalid output")
        self.assertEqual(_cause("exited", {"result": None}), "no structured output")
        state = tempfile.mkdtemp(dir=self.tmp.name)
        open(os.path.join(state, TIME_STOP_FILE), "w").close()
        self.assertEqual(_cause("exited", {"result": None}, state), "time")
        self.assertEqual(_cause("timeout", {}, state), "timeout")
        state = tempfile.mkdtemp(dir=self.tmp.name)
        open(os.path.join(state, DENY_STOP_FILE), "w").close()
        self.assertEqual(_cause("exited", {"result": None}, state), "denied")

    def write_audit(self, lines):
        state = os.path.join(self.paths["work"], "chunk-1", "state")
        os.makedirs(state, exist_ok=True)
        with open(os.path.join(state, "guard-audit.jsonl"), "w", encoding="utf-8") as f:
            f.write(lines if isinstance(lines, str) else
                    "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in lines))
        return state

    def test_denial_summary_groups_calls(self):
        def rec(tool, inp, decision="deny", reason="x"):
            return {"tool": tool, "input": inp, "decision": decision, "reason": reason}
        state = self.write_audit(
            [rec("Bash", {"command": "make -j4"})] * 3 + [rec("Bash", {"command": "cat a.c"})] * 2
            + [rec("Read", {"file_path": "/etc/x"}, reason="path outside repo: /etc/x")] * 2
            + [rec("Bash", {"command": "ls"}, "stop"), rec("Read", {}, "allow", None)])
        self.assertEqual(_denial_summary(state),
                         "거부된 호출 8건: make×3 cat×2 Read(저장소 밖)×2 ls×1")

    def test_denial_summary_ignores_missing_or_broken_audit(self):
        state = os.path.join(self.tmp.name, "nostate")
        self.assertIsNone(_denial_summary(state))
        state = self.write_audit('not json\n[1]\n{"decision":"allow"}\n{"decision":"deny","tool":5}\n')
        self.assertEqual(_denial_summary(state), "거부된 호출 1건: ?×1")
        state = self.write_audit([{"decision": "allow", "tool": "Read"}])
        self.assertIsNone(_denial_summary(state))

    def test_chunk_logs_denial_line(self):
        self.write_audit([{"tool": "Bash", "input": {"command": "make"}, "decision": "deny",
                           "reason": "r"}] * 2)
        log = io.StringIO()
        r = self.run_scn("ok", out=log)
        self.assertEqual(r["status"], "ok")
        self.assertIn("[c1] 거부된 호출 2건: make×2", log.getvalue())

    def test_chunk_survives_broken_audit(self):
        self.write_audit("\x00garbage\n")
        log = io.StringIO()
        self.assertEqual(self.run_scn("ok", out=log)["status"], "ok")
        self.assertNotIn("거부된 호출", log.getvalue())

    def test_run_all_logs_non_ok_chunk(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            run_all([self.chunk], ["p"], self.cfg("max_turns", "broken"), self.paths, self.env,
                    time.monotonic() + 120)
        self.assertIn("[c1] ✖ incomplete  max_turns → no structured output", out.getvalue())

    def test_hang_times_out_then_resumes(self):
        t = time.monotonic()
        r = self.run_scn("hang")
        self.assertLess(time.monotonic() - t, 6)
        self.assertEqual((r["error"], r["status"]), ("timeout", "ok"))
        self.assertGreaterEqual(r["stats"]["wall_s"], 2)

    def test_broken_json_incomplete(self):
        r = self.run_scn("broken", "broken")
        self.assertEqual((r["status"], r["output"]), ("incomplete", None))
        self.assertEqual(len(self.calls()), 2)

    def test_text_json_extracted(self):
        r = self.run_scn("text_json")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(len(r["output"]["findings"]), 1)
        self.assertEqual(len(self.calls()), 1)

    def test_text_json_malformed_finding_dropped(self):
        r = self.run_scn("text_json_bad")
        self.assertEqual(r["status"], "ok")
        self.assertEqual([f["title"] for f in r["output"]["findings"]], ["t"])

    def test_crash_failed(self):
        r = self.run_scn("crash")
        self.assertEqual(r["status"], "failed")
        self.assertIn("crashed", r["error"])

    def test_prompt_on_stdin(self):
        self.run_scn("ok")
        self.assertEqual(self.calls()[0]["stdin"], "PROMPT")

    def test_extract_output(self):
        self.assertIsNone(extract_output(None))
        self.assertIsNone(extract_output({"structured_output": {"a": 1}}))
        empty = {"summary": "", "findings": [], "previous_findings": [], "incomplete_reason": None,
                 "diff_reviewed": True}
        self.assertEqual(extract_output({"structured_output": {"findings": []}}), empty)
        self.assertIsNone(extract_output({"result": '{"x": 1}'}))
        self.assertEqual(extract_output({"result": 'a {"x":1} b {"findings": []}'}), empty)
        self.assertEqual(extract_output({"result": '{"findings": 1} {"findings": []}'}), empty)

    def _proc(self, script, stdin, timeout):
        info = {}
        t = time.monotonic()
        out = io.StringIO()
        st = run_process(["sh", "-c", script], self.env, self.tmp.name, stdin, timeout,
                         StreamLogger("t", out=out), None, info)
        return st, time.monotonic() - t, out.getvalue()

    def test_large_stdin_does_not_defeat_timeout(self):
        st, dt, _ = self._proc("sleep 30", "x" * 200000, 1)
        self.assertEqual(st, "timeout")
        self.assertLess(dt, 3)

    def test_lingering_grandchild_killed(self):
        st, dt, out = self._proc("sleep 20 & echo $!", "", 5)
        self.assertEqual(st, "exited")
        self.assertLess(dt, 3)
        pid = int(out.split("·")[1])
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_run_all_missing_binary_fails_every_chunk(self):
        chunks = [dict(self.chunk, index=i) for i in range(3)]
        cfg = dict(self.cfg("ok"), claude_bin=os.path.join(self.tmp.name, "nope"))
        with quiet():
            rs = run_all(chunks, ["p"] * 3, cfg, self.paths, self.env, time.monotonic() + 120)
        self.assertEqual([(r["index"], r["status"]) for r in rs], [(0, "failed"), (1, "failed"),
                                                                    (2, "failed")])
        self.assertTrue(all(r["error"].startswith("FileNotFoundError: ") for r in rs))

    def test_logger_exception_keeps_draining(self):
        class Boom(StreamLogger):
            def feed(self, line):
                raise KeyError("bad event")
        tee, err = os.path.join(self.tmp.name, "tee.jsonl"), io.StringIO()
        with contextlib.redirect_stderr(err):
            st = run_process(["sh", "-c", "for i in 1 2 3; do echo $i; done"], self.env,
                             self.tmp.name, "", 5, Boom("t", out=io.StringIO()), tee, {})
        self.assertEqual(st, "exited")
        with open(tee, encoding="utf-8") as f:
            self.assertEqual(f.read(), "1\n2\n3\n")
        self.assertEqual(err.getvalue().count("KeyError"), 1)

    def test_run_all_deadline(self):
        chunks = [dict(self.chunk, index=i) for i in range(4)]
        cfg = self.cfg("hang")
        with quiet():
            rs = run_all(chunks, ["p"] * 4, cfg, self.paths, self.env, time.monotonic() + 0.5)
        self.assertIn("deadline", [r["error"] for r in rs])
        self.assertEqual([r["index"] for r in rs], [0, 1, 2, 3])

    def test_run_all_does_not_start_a_chunk_under_a_minute_before_the_deadline(self):
        with quiet(), mock.patch("review.claude_runner.run_chunk") as rc:
            rs = run_all([self.chunk], ["p"], self.cfg("ok"), self.paths, self.env,
                         time.monotonic() + 59)
        rc.assert_not_called()
        self.assertEqual((rs[0]["status"], rs[0]["error"]), ("incomplete", "deadline"))


if __name__ == "__main__":
    unittest.main()
