"""Build the `claude -p` command line and the isolated child-process environment."""

import json
import os
from typing import Any, Dict, List, Optional

from review.config import KIT_DIR

ENV_PASSTHROUGH = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "USER")
AGENT_NAME = "pr-reviewer"
_SCHEMA_PATH = os.path.join(KIT_DIR, "config", "review-schema.json")
_SETTINGS_PATH = os.path.join(KIT_DIR, "config", "settings.json")


def load_agent(md_path: str) -> Dict[str, Dict[str, str]]:
    """Parse `---` frontmatter (`key: value` lines) and body into an agents mapping."""
    with open(md_path, "r", encoding="utf-8") as f:
        lines = f.read().split("\n")
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing frontmatter: %s" % md_path)
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise ValueError("unterminated frontmatter: %s" % md_path)
    meta = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    body = "\n".join(lines[end + 1:]).lstrip("\n")
    return {meta.get("name", AGENT_NAME): {"description": meta.get("description", ""), "prompt": body}}


def write_agents_file(md_path: str, out_dir: str) -> str:
    path = os.path.join(out_dir, "agents.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(load_agent(md_path), f, ensure_ascii=False)
    return path


def build_command(
    cfg: Dict[str, Any], agents_file: str, resume_session: Optional[str] = None
) -> List[str]:
    with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema = json.dumps(json.load(f), separators=(",", ":"), ensure_ascii=False)
    cmd = [cfg["claude_bin"], "-p", "--verbose", "--output-format", "stream-json"]
    if resume_session:
        cmd += ["--resume", resume_session]
    cmd += ["--agent", AGENT_NAME, "--agents", agents_file, "--json-schema", schema]
    cmd += ["--max-turns", "2" if resume_session else str(cfg["max_turns"])]
    cmd += ["--tools", "" if resume_session else "Read,Grep,Glob,Bash"]
    cmd += ["--permission-mode", "dontAsk", "--setting-sources", "user"]
    cmd += ["--settings", _SETTINGS_PATH, "--strict-mcp-config"]
    return cmd


def build_env(
    cfg: Dict[str, Any], repo_root: str, state_dir: str, config_dir: str, base_env: Dict[str, str]
) -> Dict[str, str]:
    """Return the complete child env; nothing outside this allow-list is inherited."""
    url = base_env.get("AI_REVIEW_LLM_BASE_URL")
    key = base_env.get("AI_REVIEW_LLM_API_KEY")
    if not url or not key:
        raise ValueError("AI_REVIEW_LLM_BASE_URL and AI_REVIEW_LLM_API_KEY are required")
    model = base_env.get("AI_REVIEW_MODEL") or cfg["model"]
    env = {k: base_env[k] for k in ENV_PASSTHROUGH if k in base_env}
    env.update({
        "CLAUDE_CONFIG_DIR": config_dir,
        "ANTHROPIC_BASE_URL": url,
        "ANTHROPIC_AUTH_TOKEN": key,
        "ANTHROPIC_MODEL": model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
        "CLAUDE_CODE_SUBAGENT_MODEL": model,
        "API_TIMEOUT_MS": str(cfg["llm_timeout_ms"]),
        "BASH_DEFAULT_TIMEOUT_MS": str(cfg["bash_timeout_ms"]),
        "BASH_MAX_TIMEOUT_MS": str(cfg["bash_timeout_ms"]),
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "AI_REVIEW_REPO_ROOT": os.path.realpath(repo_root),
        "AI_REVIEW_STATE_DIR": state_dir,
        "AI_REVIEW_EXPLORE_BUDGET": str(cfg["explore_budget"]),
        "AI_REVIEW_KIT": KIT_DIR,
    })
    # The API retry-limit env var is unconfirmed: see docs/runbook.md pending check #2.
    return env
