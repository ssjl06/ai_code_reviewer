"""PreToolUse guard: confine Claude to read-only access inside the repo and enforce an explore budget.

Self-contained (stdlib only); run as `python3 -I review/hooks/guard.py` with hook JSON on stdin.
"""
import fcntl
import hashlib
import json
import os
import re
import shlex
import sys
import time

EXPLORE_TOOLS = {"Read", "Grep", "Glob", "Bash"}
GIT_SUBCOMMANDS = {"diff", "log", "show", "blame", "grep", "ls-files"}
BUDGET_REASON = "탐색 예산 소진. 추가 도구 호출 없이 지금 최종 JSON 결과를 출력하라."
STOP_REASON = "탐색 예산 소진 — 결론 단계로 전환"
STOP_FILE = "budget-stop"  # created in the state dir once the guard stops a session
DENY_REASON = "거부된 호출이 반복됨. 추가 도구 호출 없이 지금 최종 JSON 결과를 출력하라."
DENY_STOP_REASON = "거부된 호출 반복 — 결론 단계로 전환"
DENY_STOP_FILE = "deny-stop"  # like STOP_FILE, for a stop after too many rule denials
TIME_REASON = "탐색 시간 소진. 추가 도구 호출 없이 지금 최종 JSON 결과를 출력하라."
TIME_STOP_REASON = "탐색 시간 소진 — 결론 단계로 전환"
TIME_STOP_FILE = "time-stop"  # like STOP_FILE, for a stop at AI_REVIEW_STOP_AT
ANSWER_TOOL = "StructuredOutput"  # how --json-schema delivers the final answer: never stopped
_STOPS = {STOP_REASON: BUDGET_REASON, TIME_STOP_REASON: TIME_REASON, DENY_STOP_REASON: DENY_REASON}  # stop -> shown to the model
_SHELL_META = set(";|&><$`(){}*?[]~\\\n\r")
_DIFFISH = ("--stat --shortstat --numstat --name-only --name-status --no-color --oneline -p --patch "
            "--no-patch --follow -M -w --word-diff --abbrev-commit --diff-filter= --format= --pretty=")
_HIST = _DIFFISH + " --since= --until= --author= --grep= --merges --no-merges --graph --all -n"
# subcommand -> (exact flags and "--name=" prefixes for value flags, regex for numeric forms)
_OPTS = {
    "diff": (_DIFFISH + " --cached --staged", r"-U\d+|-\d+"),
    "show": (_DIFFISH, r"-U\d+|-\d+"),
    "log": (_HIST, r"-n\d+|-\d+|-U\d+"),
    "grep": ("-n -i -w -l -c -e -E -F --line-number --ignore-case --files-with-matches "
             "--count -C -A -B", r"-[CAB]\d+|-\d+"),
    "blame": ("-w -e --porcelain --line-porcelain -L", r"-L\d+,[+-]?\d+"),
    "ls-files": ("--cached --others --exclude-standard", r"(?!)"),
}


def _option_ok(sub, tok):
    flags, numeric = _OPTS[sub]
    allowed = flags.split()
    if tok in allowed or re.fullmatch(numeric, tok):
        return True
    name, eq, _ = tok.partition("=")
    return bool(eq) and name + "=" in allowed


def _operand_ok(tok):
    if tok.startswith(("/", "~")):
        return False
    return ".." not in re.split(r"[/:]", tok)


def _deny(what, hint):
    """Rule-denial text for the model: not allowed, budget not charged, what to do instead."""
    return ("%s 쓸 수 없다. 탐색 예산은 차감되지 않았다. %s 같은 호출은 다시 시도하지 마라." % (what, hint))


_OUTSIDE_HINT = "저장소 밖의 파일은 읽을 수 없다. 저장소 안의 파일을 Read, Grep, Glob으로 보며 리뷰를 계속하라."
_NO_RUN_HINT = ("빌드, 실행, 테스트는 할 수 없고 필요하지도 않다. Read, Grep, Glob과 읽기 전용 git으로 "
                "코드를 읽고 판단하며 리뷰를 계속하라.")
_TOOL_HINTS = (
    ("cat head tail sed awk less more wc", "파일 읽기는 Read를 쓰고 리뷰를 계속하라."),
    ("ls find tree", "파일 찾기는 Glob을 쓰고 리뷰를 계속하라."),
    ("grep rg ag", "내용 검색은 Grep을 쓰고 리뷰를 계속하라."),
)


def _first_word_hint(command):
    words = command.split()
    first = words[0] if words else ""
    for names, hint in _TOOL_HINTS:
        if first in names.split():
            return hint
    return None


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
    return None if inside else _deny("이 호출은", _OUTSIDE_HINT)


def check_glob_pattern(pattern):
    if "{" in pattern or pattern.startswith(("/", "~")) or ".." in re.split(r"[\\/]", pattern):
        return _deny("이 패턴은", _OUTSIDE_HINT)
    return None


def check_bash(command):
    words = command.split()
    is_git = len(words) > 0 and words[0] == "git"
    tool_hint = _first_word_hint(command)
    if any(c in _SHELL_META for c in command):
        if tool_hint:
            return _deny("이 명령은", tool_hint)
        if is_git:
            return _deny("이 명령은", "명령은 한 번에 하나만, 파이프나 리다이렉트 없이 쓰고 리뷰를 계속하라.")
        return _deny("이 명령은", _NO_RUN_HINT)
    try:
        tokens = shlex.split(command)
    except ValueError:
        if tool_hint:
            return _deny("이 명령은", tool_hint)
        return _deny("이 명령은", "따옴표를 맞춘 단순한 명령 하나로 쓰고 리뷰를 계속하라." if is_git else _NO_RUN_HINT)
    if len(tokens) < 2 or tokens[0] != "git" or tokens[1] not in GIT_SUBCOMMANDS:
        if tool_hint:
            return _deny("이 명령은", tool_hint)
        if tokens[:1] == ["git"]:
            return _deny("이 git 명령은", "git은 %s만 쓸 수 있다. 리뷰를 계속하라."
                         % "/".join(sorted(GIT_SUBCOMMANDS)))
        return _deny("이 명령은", _NO_RUN_HINT)
    for t in tokens[2:]:
        if t == "--":
            continue
        if t.startswith("-"):
            if not _option_ok(tokens[1], t):
                return _deny("git 옵션 %s은(는)" % t, "그 옵션을 빼고 리뷰를 계속하라.")
        elif not _operand_ok(t):
            return _deny("이 명령은", _OUTSIDE_HINT)
    return None


def _bump(prefix, session_id, state_dir):
    """Add one to the session's counter file `<prefix>-<session>.json` and return the new count."""
    os.makedirs(state_dir, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "unknown")
    with open(os.path.join(state_dir, "%s-%s.json" % (prefix, safe)), "a+") as f:
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
    return count


def _call_key(event):
    """Short hash of the call's identity: tool name + tool input as canonical JSON, with the
    whitespace of a Bash command collapsed."""
    inp = event.get("tool_input")
    if not isinstance(inp, dict):
        inp = {}
    if isinstance(inp.get("command"), str):
        inp = dict(inp, command=" ".join(inp["command"].split()))
    blob = json.dumps({"tool": event.get("tool_name"), "input": inp}, sort_keys=True,
                      ensure_ascii=False, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def check_denials(reason, event, state_dir, repeat_limit, total_limit):
    """Count one rule denial for the session: `reason` until the same call has been denied
    `repeat_limit` times or `total_limit` denials of any kind have happened, then
    DENY_STOP_REASON (and DENY_STOP_FILE) for that and every later denial."""
    key = _call_key(event)
    os.makedirs(state_dir, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", event.get("session_id") or "unknown")
    with open(os.path.join(state_dir, "denied-%s.json" % safe), "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        raw = f.read()
        try:
            data = json.loads(raw) if raw.strip() else {}
            total = int(data["count"])
            seen = {str(k): int(v) for k, v in data["seen"].items()}
        except (ValueError, KeyError, TypeError, AttributeError):
            total, seen = 0, {}
        total += 1
        seen[key] = seen.get(key, 0) + 1
        f.seek(0)
        f.truncate()
        f.write(json.dumps({"count": total, "seen": seen}))
    if seen[key] < repeat_limit and total <= total_limit:
        return reason
    open(os.path.join(state_dir, DENY_STOP_FILE), "a").close()
    return DENY_STOP_REASON


def check_budget(session_id, state_dir, budget):
    """Count one explore call for the session: None within budget, BUDGET_REASON on the
    first overrun, STOP_REASON (and STOP_FILE) on later ones."""
    count = _bump("budget", session_id, state_dir)
    if count <= budget:
        return None
    if count == budget + 1:
        return BUDGET_REASON
    open(os.path.join(state_dir, STOP_FILE), "a").close()
    return STOP_REASON


def check_time(env):
    """TIME_STOP_REASON (and TIME_STOP_FILE) once the epoch time in AI_REVIEW_STOP_AT has
    passed; a missing or empty value means no time stop."""
    stop_at = env.get("AI_REVIEW_STOP_AT")
    if not stop_at or time.time() < float(stop_at):
        return None
    state_dir = env["AI_REVIEW_STATE_DIR"]
    os.makedirs(state_dir, exist_ok=True)
    open(os.path.join(state_dir, TIME_STOP_FILE), "a").close()
    return TIME_STOP_REASON


def decide(event, env):
    tool = event.get("tool_name")
    if tool == ANSWER_TOOL:
        return None
    stop = check_time(env)
    if stop:
        return stop
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
        repeat = int(env.get("AI_REVIEW_DENY_REPEAT_LIMIT", "3"))
        total = int(env.get("AI_REVIEW_DENY_TOTAL_LIMIT", "20"))
        return check_denials(reason, event, env["AI_REVIEW_STATE_DIR"], repeat, total)
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


def output(reason):
    """Hook JSON denying the call; on a stop reason also end the session (`continue` wins)."""
    stop = reason in _STOPS
    out = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": _STOPS.get(reason, reason)}}
    if stop:
        out.update({"continue": False, "stopReason": reason})
    return out


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
               "stop" if reason in _STOPS else "deny" if reason else "allow", reason)
    if reason:
        print(json.dumps(output(reason), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
