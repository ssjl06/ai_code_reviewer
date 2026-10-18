import io
import os
import unittest

from review.stream_log import StreamLogger

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
OK = os.path.join(FIX, "stream_ok.jsonl")
DENIED = os.path.join(FIX, "stream_denied.jsonl")

# Clock call order (deterministic): start() calls clock() once; feed() calls it once per
# JSON event (init, assistant, user, result each count; non-JSON lines do not);
# finish() calls it once only if no result event was seen.
# OK events: start, init, asst1, user1, asst2, user2, asst3, result
TICKS_OK = [0.0, 0.1, 7.8, 8.0, 13.9, 14.4, 29.0, 30.2]
# DENIED events: start, init, asst1, user1, result
TICKS_DENIED = [0.0, 0.1, 2.0, 2.0, 3.0]


def feed_all(fixture, ticks, status="ok"):
    out = io.StringIO()
    clock = iter(ticks).__next__
    lg = StreamLogger("c2", out=out, clock=clock, now=lambda: "12:00:00")
    lg.start("chunk 2/4  files=3  diff=18.2k tok")
    with open(fixture) as f:
        for line in f:
            lg.feed(line)
    lg.finish(status)
    return lg, out.getvalue().splitlines()


def logger(ticks):
    out = io.StringIO()
    lg = StreamLogger("c2", out=out, clock=iter(ticks).__next__, now=lambda: "12:00:00")
    return lg, out


class StreamLogTest(unittest.TestCase):
    def test_ok_stream_lines_and_stats(self):
        lg, lines = feed_all(OK, TICKS_OK)
        text = "\n".join(lines)
        self.assertTrue(lines[0].startswith("12:00:00 [c2] ● start"))
        self.assertEqual(sum("◆ LLM" in l for l in lines), 3)
        self.assertIn("◆ LLM    7.8s  in=21.4k out=312  (40 tok/s)", text)
        self.assertIn("◆ LLM    5.9s", text)
        self.assertIn("◆ LLM    14.6s", text)
        self.assertIn('▶ T1 Grep  pattern="launch_kernel" path=src/', text)
        self.assertRegex(text, r"✔ T1 Grep  0\.2s  14 lines")
        self.assertRegex(text, r"✔ T2 Bash  0\.5s  2 lines")
        self.assertIn("■ done   success  turns=3  findings=3  wall=30.2s  api=28.3s", lines[-1])
        self.assertEqual(len(lines), 9)  # nothing printed by finish() after result
        s = lg.stats()
        self.assertEqual((s["tool_calls"], s["denied"], s["turns"]), (2, 0, 3))
        self.assertAlmostEqual(s["llm_s"], 28.3, places=1)
        self.assertAlmostEqual(s["tool_s"], 0.7, places=1)
        self.assertEqual((s["in_tokens"], s["out_tokens"]), (66400, 1362))
        self.assertAlmostEqual(s["wall_s"], 30.2, places=1)
        self.assertEqual(lg.session_id, "sess-ok")

    def test_denied_and_max_turns(self):
        lg, lines = feed_all(DENIED, TICKS_DENIED)
        text = "\n".join(lines)
        self.assertIn("✖ T1 Read", text)
        self.assertIn("denied: path outside repo: /etc/hosts", text)
        self.assertEqual(lg.stats()["denied"], 1)
        self.assertEqual(lg.stats()["result"]["subtype"], "error_max_turns")
        self.assertIn("findings=0", lines[-1])

    def test_non_guard_error_prefix(self):
        lg, out = logger([0, 1, 2])
        lg.start("x")
        lg.feed('{"type":"assistant","message":{"id":"m","content":[{"type":"tool_use","id":"t","name":"Read","input":{"file_path":"a","offset":5,"limit":10}}]}}')
        lg.feed('{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t","content":"File not found","is_error":true}]}}')
        text = out.getvalue()
        self.assertIn("▶ T1 Read  a offset=5 limit=10", text)
        self.assertIn("error: File not found", text)
        self.assertEqual(lg.stats()["denied"], 0)

    def test_read_only_git_message_is_denied(self):
        lg, out = logger([0, 1, 2])
        lg.start("x")
        lg.feed('{"type":"assistant","message":{"id":"m","content":[{"type":"tool_use","id":"t","name":"Bash","input":{"command":"git push"}}]}}')
        lg.feed('{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t","content":"only read-only git diff/log allowed","is_error":true}]}}')
        self.assertEqual(lg.stats()["denied"], 1)

    def test_same_message_id_counted_once(self):
        lg, out = logger([0, 4.0, 5.0])
        lg.start("x")
        ev = '{"type":"assistant","message":{"id":"m1","content":[{"type":"text","text":"a"}],"usage":{"input_tokens":10,"output_tokens":5}}}'
        lg.feed(ev)
        lg.feed(ev)
        self.assertEqual(out.getvalue().count("◆ LLM"), 1)
        s = lg.stats()
        self.assertEqual((s["turns"], s["in_tokens"], s["out_tokens"]), (1, 10, 5))

    def test_non_json_line_passthrough(self):
        lg, out = logger([0])
        lg.start("x")
        lg.feed("warning: x\n")
        self.assertEqual(out.getvalue().splitlines()[-1], "12:00:00 [c2] · warning: x")

    def test_finish_without_result(self):
        lg, out = logger([0, 5.0])
        lg.start("x")
        lg.finish("incomplete", "timeout")
        self.assertEqual(out.getvalue().splitlines()[-1], "12:00:00 [c2] ■ done   incomplete  timeout")
        self.assertIsNone(lg.stats()["result"])
        self.assertAlmostEqual(lg.stats()["wall_s"], 5.0)


if __name__ == "__main__":
    unittest.main()
