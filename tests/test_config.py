import contextlib
import io
import json
import os
import tempfile
import unittest

from review.config import load_config


class ConfigTest(unittest.TestCase):
    def test_defaults_have_spec_values(self):
        c = load_config()
        self.assertEqual(c["chunk_max_tokens"], 20000)
        self.assertEqual(c["max_chunks"], 40)
        self.assertEqual(c["concurrency"], 3)
        self.assertEqual(c["max_turns"], 60)
        self.assertEqual(c["explore_budget"], 50)
        self.assertEqual(c["deny_repeat_limit"], 3)
        self.assertEqual(c["deny_total_limit"], 20)
        self.assertEqual(c["deadline_s"], 18000)
        self.assertEqual((c["llm_timeout_ms"], c["fallback_timeout_s"], c["chunk_timeout_s"]),
                         (600000, 600, 3600))
        self.assertEqual(c["block_severities"], ["critical", "major"])
        self.assertEqual(c["block_confidence"], 80)
        self.assertEqual(c["min_confidence"], 50)
        self.assertEqual(c["exclude_globs"], [
            "vendor/**", "third_party/**", "**/*.lock", "**/package-lock.json",
            "**/*.min.js", "**/*.pb.go", "**/*_pb2.py", ".github/ai-review/**"])

    def test_file_and_overrides_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cfg.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"concurrency": 2}, f)
            c = load_config(path, {"max_chunks": 4})
        self.assertEqual((c["concurrency"], c["max_chunks"]), (2, 4))
        self.assertEqual(c["max_turns"], 60)

    def test_max_turns_raised_to_fit_budget(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            c = load_config(None, {"explore_budget": 15, "max_turns": 16})
        self.assertEqual(c["max_turns"], 19)
        self.assertIn("warning: max_turns 16", err.getvalue())
        with contextlib.redirect_stderr(io.StringIO()) as quiet:
            self.assertEqual(load_config(None, {"max_turns": 54})["max_turns"], 54)
        self.assertEqual(quiet.getvalue(), "")

    def test_retired_deny_limit_is_ignored_with_a_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cfg.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"deny_limit": 9, "concurrency": 2}, f)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                c = load_config(path, {"deny_limit": 7})
        self.assertNotIn("deny_limit", c)
        self.assertEqual((c["concurrency"], c["deny_repeat_limit"], c["deny_total_limit"]), (2, 3, 20))
        self.assertEqual(err.getvalue().count("deny_limit"), 2)

    def test_unknown_key_rejected(self):
        with self.assertRaises(KeyError):
            load_config(None, {"max_turn": 5})

    def test_unknown_key_in_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cfg.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"max_turn": 5}, f)
            with self.assertRaises(KeyError):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
