#!/usr/bin/env python3
"""Fake `claude -p`: fake_claude.py [--scenario S] [--resume-scenario S] [--log F] <claude argv...>"""
import json
import os
import re
import subprocess
import sys
import time

OUT = {"summary": "s", "findings": [{"file": "a.c", "line": 1, "severity": "major",
                                     "confidence": 90, "category": "bug", "title": "t",
                                     "detail": "d"}],
       "previous_findings": [], "incomplete_reason": None}
BAD_FINDING = {"line": "x", "severity": "major", "confidence": 90, "title": "no file"}

BLOCKING = {"file": "src/k.cu", "line": 2, "severity": "major", "confidence": 85,
            "category": "memory", "title": "배열 범위를 벗어난 접근",
            "detail": "p[1]은 길이 1 버퍼에서 범위를 벗어난다."}

EN_FINDING = {"file": "src/k.cu", "line": 2, "severity": "major", "confidence": 85,
              "category": "memory", "title": "Out-of-bounds read",
              "detail": "`p[1]` reads past the end of a one-element buffer.",
              "suggestion": "Check the length before indexing."}
ENGLISH = {"summary": "Found one out-of-bounds read in the kernel.", "findings": [EN_FINDING],
           "previous_findings": [], "incomplete_reason": None}
KO_FINDING = dict(EN_FINDING, title="범위를 벗어난 읽기",
                  detail="`p[1]`은 길이 1 버퍼의 끝을 넘어 읽는다.",
                  suggestion="인덱싱 전에 길이를 확인한다.")
KOREAN = dict(ENGLISH, summary="커널에서 범위를 벗어난 읽기 1건을 찾았다.", findings=[KO_FINDING])
KOREAN_MOVED = dict(KOREAN, findings=[dict(KO_FINDING, line=3)])
KOREAN_ASK = "서술 필드만 한국어로"  # marker phrase of review.prompt.KOREAN_PROMPT


def scenario_output(scn, stdin):
    if scn == "blocking":
        return {"summary": "차단 1건", "findings": [BLOCKING], "previous_findings": [],
                "incomplete_reason": None}
    ids = re.findall(r'"id": "([^"]+)"', stdin)  # previous findings JSON in the prompt
    return {"summary": "해결 확인", "findings": [],
            "previous_findings": [{"id": i, "status": "resolved"} for i in ids],
            "incomplete_reason": None}


def emit(ev):
    print(json.dumps(ev), flush=True)


def result(sid, subtype="success", **extra):
    ev = {"type": "result", "subtype": subtype, "is_error": subtype != "success", "num_turns": 2,
          "duration_ms": 10, "duration_api_ms": 5, "session_id": sid}
    ev.update(extra)
    emit(ev)


def budget_stop(sid):
    """Call Read through the real guard hook until it stops the session, like claude would."""
    guard = os.path.join(os.environ["AI_REVIEW_KIT"], "review", "hooks", "guard.py")
    path = os.path.join(os.environ["AI_REVIEW_REPO_ROOT"], "a.c")
    for n in range(1, 100):
        tid = "tu-%d" % n
        emit({"type": "assistant", "session_id": sid, "message": {"id": "m%d" % n, "content": [
            {"type": "tool_use", "id": tid, "name": "Read", "input": {"file_path": path}}]}})
        ev = {"session_id": sid, "tool_name": "Read", "tool_input": {"file_path": path}}
        out = subprocess.run(["python3", "-I", guard], input=json.dumps(ev), text=True,
                             capture_output=True, env=os.environ).stdout
        hook = json.loads(out) if out.strip() else {}
        reason = (hook.get("hookSpecificOutput") or {}).get("permissionDecisionReason")
        emit({"type": "user", "session_id": sid, "message": {"content": [
            {"type": "tool_result", "tool_use_id": tid, "is_error": bool(reason),
             "content": "PreToolUse:Read hook error: %s" % reason if reason else "x"}]}})
        if hook.get("continue") is False:  # observed claude 2.1.x: success, empty result
            return result(sid, result="", terminal_reason="hook_stopped")


def main():
    argv = sys.argv[1:]
    opts = {"--scenario": "ok", "--resume-scenario": "ok", "--log": None}
    while argv and argv[0] in opts:
        opts[argv[0]] = argv[1]
        argv = argv[2:]
    stdin = sys.stdin.read()
    if opts["--log"]:
        with open(opts["--log"], "a", encoding="utf-8") as f:
            f.write(json.dumps({"argv": argv, "stdin": stdin,
                                "stop_at": os.environ.get("AI_REVIEW_STOP_AT")}) + "\n")
    scn = opts["--resume-scenario"] if "--resume" in argv else opts["--scenario"]
    sid = argv[argv.index("--resume") + 1] if "--resume" in argv else "sess-fake"
    if scn == "crash":
        print("boom: crashed", file=sys.stderr)
        sys.exit(2)
    emit({"type": "system", "subtype": "init", "session_id": sid, "model": "fake"})
    if scn == "hang":
        time.sleep(60)
    elif scn == "ok":
        result(sid, structured_output=OUT)
    elif scn == "noted":
        result(sid, structured_output=dict(OUT, incomplete_reason="탐색 예산 부족"))
    elif scn == "silent":
        result(sid, structured_output={"summary": "s", "findings": [],
                                       "previous_findings": [], "incomplete_reason": None})
    elif scn in ("blocking", "resolved"):
        result(sid, structured_output=scenario_output(scn, stdin))
    elif scn == "english":
        result(sid, structured_output=ENGLISH)
    elif scn == "english_then_korean":  # the fallback answer is English, the re-ask is Korean
        result(sid, structured_output=KOREAN if KOREAN_ASK in stdin else ENGLISH)
    elif scn in ("korean", "korean_moved"):  # resume answers only the Korean re-ask prompt
        if KOREAN_ASK in stdin:
            result(sid, structured_output=KOREAN if scn == "korean" else KOREAN_MOVED)
        else:
            result(sid, "error_max_turns")
    elif scn in ("budget_stop", "time_stop"):  # which one depends on the guard env
        budget_stop(sid)
    elif scn == "max_turns":
        result(sid, "error_max_turns")
    elif scn == "broken":
        result(sid, result="not json at all")
    elif scn == "text_json":
        result(sid, result="Here: " + json.dumps(OUT) + " done")
    elif scn == "text_json_bad":
        bad = dict(OUT, findings=OUT["findings"] + [BAD_FINDING])
        result(sid, result="Here: " + json.dumps(bad) + " done")


main()
