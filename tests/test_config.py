import json
import os
import tempfile
import unittest

from review.config import load_config


class ConfigTest(unittest.TestCase):
    def test_defaults_have_spec_values(self):
        c = load_config()
        self.assertEqual(c["chunk_max_tokens"], 20000)
        self.assertEqual(c["max_chunks"], 6)
        self.assertEqual(c["concurrency"], 3)
        self.assertEqual(c["max_turns"], 10)
        self.assertEqual(c["explore_budget"], 6)
        self.assertEqual(c["deadline_s"], 1200)
        self.assertEqual(c["block_severities"], ["critical", "major"])
        self.assertEqual(c["block_confidence"], 80)
        self.assertEqual(c["min_confidence"], 50)

    def test_file_and_overrides_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cfg.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"concurrency": 2}, f)
            c = load_config(path, {"max_chunks": 4})
        self.assertEqual((c["concurrency"], c["max_chunks"]), (2, 4))
        self.assertEqual(c["max_turns"], 10)

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
