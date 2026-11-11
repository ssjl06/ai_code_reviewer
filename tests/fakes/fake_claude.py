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
       "previous_findings": [], "incomplete_reason": None, "diff_reviewed": True}
BAD_FINDING = {"line": "x", "severity": "major", "confidence": 90, "title": "no file"}

BLOCKING = {"file": "src/k.cu", "line": 2, "severity": "major", "confidence": 85,
            "category": "memory", "title": "배열 범위를 벗어난 접근",
            "detail": "p[1]은 길이 1 버퍼에서 범위를 벗어난다."}

EN_FINDING = {"file": "src/k.cu", "line": 2, "severity": "major", "confidence": 85,
              "category": "memory", "title": "Out-of-bounds read",
              "detail": "`p[1]` reads past the end of a one-element buffer.",
              "suggestion": "Check the length before indexing."}
ENGLISH = {"summary": "Found one out-of-bounds read in the kernel.", "findings": [EN_FINDING],
           "previous_findings": [], "incomplete_reason": None, "diff_reviewed": True}
KO_FINDING = dict(EN_FINDING, title="범위를 벗어난 읽기",
                  detail="`p[1]`은 길이 1 버퍼의 끝을 넘어 읽는다.",
                  suggestion="인덱싱 전에 길이를 확인한다.")
KOREAN = dict(ENGLISH, summary="커널에서 범위를 벗어난 읽기 1건을 찾았다.", findings=[KO_FINDING])
KOREAN_MOVED = dict(KOREAN, findings=[dict(KO_FINDING, line=3)])
KOREAN_ASK = "서술 필드만 한국어로"  # marker phrase of review.prompt.KOREAN_PROMPT


MENTION = {"reply": "확인했습니다. 이 이슈는 철회해도 됩니다.",
           "verdicts": [{"id": "a1", "withdraw": True, "reason": "호출부에서 길이를 검사한다"}],
           "new_issues": [], "guideline": None, "wants": None}
MENTION_EN = dict(MENTION, reply="Checked. This issue can be withdrawn.",
                  verdicts=[{"id": "a1", "withdraw": True, "reason": "The caller checks the length"}])
MENTION_ASK = "reply, reason"  # marker phrase of review.prompt.MENTION_KOREAN


def scenario_output(scn, stdin):
    if scn == "blocking":
        return {"summary": "차단 1건", "findings": [BLOCKING], "previous_findings": [],
                "incomplete_reason": None, "diff_reviewed": True}
    ids = re.findall(r'"id": "([^"]+)"', stdin)  # previous findings JSON in the prompt
    return {"summary": "해결 확인", "findings": [],
            "previous_findings": [{"id": i, "status": "resolved"} for i in ids],
            "incomplete_reason": None, "diff_reviewed": True}


def scripted_output(stdin, log):
    """Scenario "script": `script.json` next to the call log holds a JSON list of rules `{"when": substring or
    null, "findings": [...], "resolve": [ids], "resolve_rechecked": bool}`. The first rule whose
    `when` occurs in the prompt answers; a prompt no rule matches gets an empty answer. A rule with
    `"unreviewed": true` answers `diff_reviewed: false`, which makes the chunk `partial`."""
    with open(os.path.join(os.path.dirname(log), "script.json"), encoding="utf-8") as f:
        rules = json.load(f)
    rule = next((r for r in rules if not r.get("when") or r["when"] in stdin), {})
    ids = list(rule.get("resolve", []))
    if rule.get("resolve_rechecked"):  # the ids of the recheck sections of the prompt
        ids += re.findall(r"^이슈 (\w+), ", stdin, re.M)
    return {"summary": "s", "findings": rule.get("findings", []),
            "previous_findings": [{"id": i, "status": "resolved"} for i in ids],
            "incomplete_reason": None, "diff_reviewed": not rule.get("unreviewed")}


def _silent():
    return {"summary": "s", "findings": [], "previous_findings": [], "incomplete_reason": None,
            "diff_reviewed": True}


def mention_script(argv, stdin, log):
    """Scenario "mscript": `mention.json` next to the call log holds `{"review": review output
    or null, "mention": [rules]}`. A review session answers `review` (null: no findings). A
    mention session answers the first rule whose `when` (substring or null) occurs in the
    prompt: `out` (null: no structured output), after `sleep` seconds; `withdraw_all` adds a
    `withdraw: true` verdict for every 8-hex id in the prompt."""
    with open(os.path.join(os.path.dirname(log), "mention.json"), encoding="utf-8") as f:
        spec = json.load(f)
    if "pr-mention" not in argv:
        return spec.get("review") or _silent()
    rule = next((r for r in spec.get("mention") or []
                 if not r.get("when") or r["when"] in stdin), {})
    if rule.get("sleep"):
        time.sleep(rule["sleep"])
    out = rule.get("out")
    if out is not None and rule.get("withdraw_all"):
        ids = dict.fromkeys(re.findall(r"(?<![0-9a-f])[0-9a-f]{8}(?![0-9a-f])", stdin))
        out = dict(out, verdicts=[{"id": i, "withdraw": True, "reason": "철회"} for i in ids])
    return out


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
                                "stop_at": os.environ.get("AI_REVIEW_STOP_AT"),
                                "config_dir": os.environ.get("CLAUDE_CONFIG_DIR"),
                                "env_keys": sorted(os.environ)}) + "\n")
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
    elif scn == "unreviewed":
        result(sid, structured_output=dict(OUT, diff_reviewed=False,
                                           incomplete_reason="변경 줄을 판단하지 못했다"))
    elif scn == "silent":
        result(sid, structured_output={"summary": "s", "findings": [],
                                       "previous_findings": [], "incomplete_reason": None,
                                       "diff_reviewed": True})
    elif scn in ("blocking", "resolved"):
        result(sid, structured_output=scenario_output(scn, stdin))
    elif scn == "script":
        result(sid, structured_output=scripted_output(stdin, opts["--log"]))
    elif scn == "mscript":
        out = mention_script(argv, stdin, opts["--log"])
        if out is None:
            result(sid, "error_max_turns")
        else:
            result(sid, structured_output=out)
    elif scn == "english":
        result(sid, structured_output=ENGLISH)
    elif scn == "english_then_korean":  # the fallback answer is English, the re-ask is Korean
        result(sid, structured_output=KOREAN if KOREAN_ASK in stdin else ENGLISH)
    elif scn in ("korean", "korean_moved"):  # resume answers only the Korean re-ask prompt
        if KOREAN_ASK in stdin:
            result(sid, structured_output=KOREAN if scn == "korean" else KOREAN_MOVED)
        else:
            result(sid, "error_max_turns")
    elif scn == "mention":
        result(sid, structured_output=MENTION)
    elif scn == "mention_en":
        result(sid, structured_output=MENTION_EN)
    elif scn == "mention_korean":  # resume answers only the Korean re-ask prompt
        result(sid, structured_output=MENTION if MENTION_ASK in stdin else MENTION_EN)
    elif scn in ("budget_stop", "time_stop"):  # which one depends on the guard env
        budget_stop(sid)
    elif scn == "max_turns":
        result(sid, "error_max_turns")
    elif scn == "broken":
        result(sid, result="not json at all")
    elif scn == "text_json":
        result(sid, result="Here: " + json.dumps(OUT) + " done")
    elif scn in ("text_missing", "text_null", "text_false_str"):  # plain text, no schema
        raw = {k: v for k, v in OUT.items() if k != "diff_reviewed"}
        if scn != "text_missing":
            raw["diff_reviewed"] = None if scn == "text_null" else "false"
        result(sid, result="Here: " + json.dumps(raw) + " done")
    elif scn == "text_json_bad":
        bad = dict(OUT, findings=OUT["findings"] + [BAD_FINDING])
        result(sid, result="Here: " + json.dumps(bad) + " done")


main()
