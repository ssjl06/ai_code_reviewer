import contextlib
import io
import json
import os
import tempfile
import time
import unittest

from review.claude_runner import extract_output, run_all, run_chunk, run_process
from review.stream_log import StreamLogger
from review.prompt import FALLBACK_PROMPT
from tests.fakes import make_wrapper


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
                "llm_timeout_ms": 1000, "bash_timeout_ms": 1000, "explore_budget": 6,
                "model": "m", "concurrency": 1}

    def run_scn(self, scn, resume="ok"):
        with quiet():
            return run_chunk(self.chunk, "PROMPT", 1, self.cfg(scn, resume), self.paths, self.env)

    def calls(self):
        with open(self.log, encoding="utf-8") as f:
            return [json.loads(x) for x in f]

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

    def test_crash_failed(self):
        r = self.run_scn("crash")
        self.assertEqual(r["status"], "failed")
        self.assertIn("crashed", r["error"])

    def test_prompt_on_stdin(self):
        self.run_scn("ok")
        self.assertEqual(self.calls()[0]["stdin"], "PROMPT")

    def test_extract_output(self):
        self.assertIsNone(extract_output(None))
        self.assertEqual(extract_output({"structured_output": {"a": 1}}), {"a": 1})
        self.assertIsNone(extract_output({"result": '{"x": 1}'}))
        self.assertEqual(extract_output({"result": 'a {"x":1} b {"findings": []}'}), {"findings": []})

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

    def test_run_all_deadline(self):
        chunks = [dict(self.chunk, index=i) for i in range(4)]
        cfg = self.cfg("hang")
        with quiet():
            rs = run_all(chunks, ["p"] * 4, cfg, self.paths, self.env, time.monotonic() + 0.5)
        self.assertIn("deadline", [r["error"] for r in rs])
        self.assertEqual([r["index"] for r in rs], [0, 1, 2, 3])


if __name__ == "__main__":
    unittest.main()
