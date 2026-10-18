"""PreToolUse guard: confine Claude to read-only access inside the repo and enforce an explore budget.

Self-contained (stdlib only); run as `python3 -I review/hooks/guard.py` with hook JSON on stdin.
"""
import fcntl
import json
import os
import re
import shlex
import sys
import time

EXPLORE_TOOLS = {"Read", "Grep", "Glob", "Bash"}
GIT_SUBCOMMANDS = {"diff", "log", "show", "blame", "grep", "ls-files"}
BUDGET_REASON = "탐색 예산 소진. 추가 도구 호출 없이 지금 최종 JSON 결과를 출력하라."
_SHELL_META = set(";|&><$`()\n\r")
_BAD_FLAGS = ("--output", "--git-dir", "--work-tree", "--no-index", "--ext-diff",
              "--exec-path", "--open-files-in-pager", "-O")


def check_path(path, root):
    """Deny reason if `path` resolves (symlinks included) outside `root`, else None."""
    if path is None:
        return None
    real_root = os.path.realpath(root)
    p = os.path.expanduser(path)
    if not os.path.isabs(p):
        p = os.path.join(real_root, p)
    real = os.path.realpath(p)
    try:
        inside = os.path.commonpath([real, real_root]) == real_root
    except ValueError:
        inside = False
    return None if inside else "path outside repo: %s" % path


def check_glob_pattern(pattern):
    if pattern.startswith(("/", "~")) or ".." in re.split(r"[\\/]", pattern):
        return "glob pattern outside repo: %s" % pattern
    return None


def check_bash(command):
    if any(c in _SHELL_META for c in command):
        return "shell metacharacters not allowed"
    try:
        tokens = shlex.split(command)
    except ValueError:
        return "unparseable command"
    if len(tokens) < 2 or tokens[0] != "git" or tokens[1] not in GIT_SUBCOMMANDS:
        return "only read-only git %s allowed" % "/".join(sorted(GIT_SUBCOMMANDS))
    for t in tokens[1:]:
        if t in ("-c", "-C") or t.startswith(_BAD_FLAGS):
            return "git option not allowed: %s" % t
    return None


def check_budget(session_id, state_dir, budget):
    """Count one explore call for the session; return BUDGET_REASON once over budget."""
    os.makedirs(state_dir, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "unknown")
    with open(os.path.join(state_dir, "budget-%s.json" % safe), "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        raw = f.read()
        try:
            count = int(json.loads(raw)["count"]) if raw.strip() else 0
        except (ValueError, KeyError, TypeError):
            count = 0
        count += 1
        f.seek(0)
        f.truncate()
        f.write(json.dumps({"count": count}))
    return BUDGET_REASON if count > budget else None


def decide(event, env):
    tool = event.get("tool_name")
    if tool not in EXPLORE_TOOLS:
        return None
    inp = event.get("tool_input") or {}
    root = env["AI_REVIEW_REPO_ROOT"]
    reason = None
    if tool == "Read":
        reason = check_path(inp.get("file_path"), root)
    elif tool == "Grep":
        reason = check_path(inp.get("path"), root)
    elif tool == "Glob":
        reason = check_path(inp.get("path"), root)
        if reason is None and inp.get("pattern"):
            reason = check_glob_pattern(inp["pattern"])
    else:
        reason = check_bash(inp.get("command") or "")
    if reason:
        return reason
    budget = int(env.get("AI_REVIEW_EXPLORE_BUDGET", "6"))
    return check_budget(event.get("session_id", ""), env["AI_REVIEW_STATE_DIR"], budget)


def _audit(state_dir, event, decision, reason):
    try:
        os.makedirs(state_dir, exist_ok=True)
        rec = {"ts": time.time(), "session_id": event.get("session_id"),
               "tool": event.get("tool_name"), "input": event.get("tool_input"),
               "decision": decision, "reason": reason}
        with open(os.path.join(state_dir, "guard-audit.jsonl"), "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def main():
    event = {}
    try:
        event = json.loads(sys.stdin.read())
        if not isinstance(event, dict):
            raise ValueError("event not an object")
        reason = decide(event, os.environ)
    except Exception as e:
        reason = "guard error: %s" % type(e).__name__
    state_dir = os.environ.get("AI_REVIEW_STATE_DIR")
    if state_dir:
        _audit(state_dir, event if isinstance(event, dict) else {},
               "deny" if reason else "allow", reason)
    if reason:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": reason}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
