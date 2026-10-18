#!/usr/bin/env python3
"""Fake `claude -p`: fake_claude.py [--scenario S] [--resume-scenario S] [--log F] <claude argv...>"""
import json
import re
import sys
import time

OUT = {"summary": "s", "findings": [{"file": "a.c", "line": 1, "severity": "major",
                                     "confidence": 90, "title": "t"}], "incomplete_reason": None}

BLOCKING = {"file": "src/k.cu", "line": 2, "severity": "major", "confidence": 85,
            "category": "memory", "title": "배열 범위를 벗어난 접근",
            "detail": "p[1]은 길이 1 버퍼에서 범위를 벗어난다."}


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


def main():
    argv = sys.argv[1:]
    opts = {"--scenario": "ok", "--resume-scenario": "ok", "--log": None}
    while argv and argv[0] in opts:
        opts[argv[0]] = argv[1]
        argv = argv[2:]
    stdin = sys.stdin.read()
    if opts["--log"]:
        with open(opts["--log"], "a", encoding="utf-8") as f:
            f.write(json.dumps({"argv": argv, "stdin": stdin}) + "\n")
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
    elif scn in ("blocking", "resolved"):
        result(sid, structured_output=scenario_output(scn, stdin))
    elif scn == "max_turns":
        result(sid, "error_max_turns")
    elif scn == "broken":
        result(sid, result="not json at all")
    elif scn == "text_json":
        result(sid, result="Here: " + json.dumps(OUT) + " done")


main()
