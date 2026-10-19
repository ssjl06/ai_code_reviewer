import json
import os
import tempfile
import unittest

from review.claude_cmd import (
    ENV_PASSTHROUGH,
    build_command,
    build_env,
    load_agent,
    write_agents_file,
)
from review.config import KIT_DIR, load_config

CFG = load_config()
AGENT_MD = os.path.join(KIT_DIR, "agent/pr-reviewer.md")
LLM = {"AI_REVIEW_LLM_BASE_URL": "http://llm:4000", "AI_REVIEW_LLM_API_KEY": "k"}


class ClaudeCmdTest(unittest.TestCase):
    def test_load_agent(self):
        a = load_agent(AGENT_MD)["pr-reviewer"]
        self.assertTrue(a["description"])
        self.assertIn("## 역할", a["prompt"])
        self.assertFalse(a["prompt"].startswith("---"))

    def test_write_agents_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_agents_file(AGENT_MD, d)
            self.assertEqual(p, os.path.join(d, "agents.json"))
            with open(p, encoding="utf-8") as f:
                self.assertIn("pr-reviewer", json.load(f))

    def test_command_normal(self):
        c = build_command(CFG, "/t/agents.json")
        self.assertEqual(c[:5], ["claude", "-p", "--verbose", "--output-format", "stream-json"])
        self.assertEqual(c[c.index("--agent") + 1], "pr-reviewer")
        self.assertEqual(c[c.index("--agents") + 1], "/t/agents.json")
        self.assertEqual(c[c.index("--max-turns") + 1], "10")
        self.assertEqual(c[c.index("--tools") + 1], "Read,Grep,Glob,Bash")
        self.assertEqual(c[c.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(c[c.index("--setting-sources") + 1], "user")
        self.assertEqual(c[c.index("--settings") + 1], os.path.join(KIT_DIR, "config/settings.json"))
        self.assertEqual(c[-1], "--strict-mcp-config")
        schema = c[c.index("--json-schema") + 1]
        json.loads(schema)
        self.assertNotIn("--resume", c)

    def test_command_resume(self):
        c = build_command(CFG, "/t/agents.json", resume_session="abc")
        self.assertEqual(c[c.index("--resume") + 1], "abc")
        self.assertEqual(c[c.index("--tools") + 1], "")
        self.assertEqual(c[c.index("--max-turns") + 1], "2")

    def test_env_has_no_github_tokens(self):
        base = dict(LLM, PATH="/bin", HOME="/h", AI_REVIEW_BOT_PAT="x", GITHUB_TOKEN="y", GH_TOKEN="z")
        e = build_env(CFG, "/repo", "/state", "/cfg", base)
        for bad in ["AI_REVIEW_BOT_PAT", "GITHUB_TOKEN", "GH_TOKEN"]:
            self.assertNotIn(bad, e)
        self.assertNotIn("x", e.values())
        self.assertNotIn("y", e.values())
        self.assertEqual(e["ANTHROPIC_BASE_URL"], "http://llm:4000")
        self.assertEqual(e["ANTHROPIC_AUTH_TOKEN"], "k")
        self.assertEqual(e["ANTHROPIC_MODEL"], "deepseek-v4-flash")
        self.assertEqual(e["API_TIMEOUT_MS"], "180000")
        self.assertEqual(e["BASH_MAX_TIMEOUT_MS"], "60000")
        self.assertEqual(e["AI_REVIEW_EXPLORE_BUDGET"], "6")
        self.assertEqual(e["AI_REVIEW_KIT"], KIT_DIR)
        self.assertEqual(e["CLAUDE_CONFIG_DIR"], "/cfg")
        self.assertEqual(e["AI_REVIEW_REPO_ROOT"], os.path.realpath("/repo"))
        self.assertTrue(all(isinstance(v, str) for v in e.values()))

    def test_env_exact_keys_and_model_override(self):
        base = dict(LLM, PATH="/bin", AI_REVIEW_MODEL="m2", FAKE_X="1")
        e = build_env(CFG, "/repo", "/s", "/c", base)
        self.assertNotIn("FAKE_X", e)
        self.assertNotIn("HOME", e)
        self.assertEqual(e["CLAUDE_CODE_SUBAGENT_MODEL"], "m2")
        self.assertEqual(e["ANTHROPIC_DEFAULT_OPUS_MODEL"], "m2")
        self.assertIn("PATH", ENV_PASSTHROUGH)
        for k in ("DISABLE_AUTOUPDATER", "DISABLE_TELEMETRY", "DISABLE_ERROR_REPORTING",
                  "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"):
            self.assertEqual(e[k], "1")

    def test_env_requires_llm(self):
        with self.assertRaises(ValueError):
            build_env(CFG, "/repo", "/s", "/c", {"PATH": "/bin"})
        with self.assertRaises(ValueError):
            build_env(CFG, "/repo", "/s", "/c", {"AI_REVIEW_LLM_BASE_URL": "u"})


if __name__ == "__main__":
    unittest.main()
