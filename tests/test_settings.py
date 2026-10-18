import json
import os
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SettingsTest(unittest.TestCase):
    def test_settings_rules(self):
        with open(os.path.join(KIT, "config", "settings.json")) as f:
            s = json.load(f)
        allow, deny = s["permissions"]["allow"], s["permissions"]["deny"]
        for r in ["Read(./**)", "Grep(./**)", "Glob(./**)", "Bash(git diff:*)", "Bash(git log:*)",
                  "Bash(git show:*)", "Bash(git blame:*)", "Bash(git grep:*)",
                  "Bash(git ls-files:*)"]:
            self.assertIn(r, allow)
        for r in ["Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch", "Bash(git push:*)",
                  "Bash(git commit:*)", "Bash(git checkout:*)", "Bash(git reset:*)",
                  "Bash(git config:*)", "Bash(git diff --no-index:*)"]:
            self.assertIn(r, deny)
        self.assertEqual(s["permissions"]["defaultMode"], "dontAsk")
        hook = s["hooks"]["PreToolUse"][0]
        self.assertEqual(hook["matcher"], "*")
        self.assertEqual(hook["hooks"][0]["type"], "command")
        self.assertEqual(hook["hooks"][0]["timeout"], 10)
        self.assertIn("review/hooks/guard.py", hook["hooks"][0]["command"])


if __name__ == "__main__":
    unittest.main()
