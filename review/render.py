"""Render the PR review: Korean body, inline comments, state marker, step summary."""
from __future__ import annotations

import json
import re

from review.ledger import SEV_RANK

MARKER = "ai-review-state"
MAX_BODY = 60000
_OPEN = "<!-- " + MARKER + " "
_HEADS = {"APPROVE": "✅ 승인", "REQUEST_CHANGES": "❌ 변경 요청", "COMMENT": "💬 코멘트"}
_PREV = {"resolved": "✅ 해결", "open": "⏳ 미해결", "unclear": "❔ 불명확"}
_SEVS = ["critical", "major", "minor", "info"]
_CUT = "\n\n(본문 길이 제한으로 일부 생략)"
FOLDED_MAX = 20     # entries of the folded section
FOLDED_DETAIL = 200  # characters of detail shown per entry
_PREV_HEAD = "### 이전 지적 처리 현황"
_FOLDED_HEAD = "### 기존 이슈에 합쳐진 지적"
_LATE_HEADS = ("### 조각별 요약", "### 미완료/실패 원인", "### 리뷰 범위")
_SEV_KO = {"critical": "치명", "major": "중대", "minor": "경미", "info": "참고"}
_CAT_KO = {"bug": "버그", "security": "보안", "concurrency": "동시성", "memory": "메모리",
           "performance": "성능", "api-misuse": "API 오용", "maintainability": "유지보수",
           "docs": "문서"}
# chunk termination codes (claude_runner: "<main cause>" or "<main cause> → <fallback outcome>")
_CAUSES = {"timeout": "시간 초과", "deadline": "전체 시간 한도 도달", "max_turns": "턴 한도 도달",
           "budget": "탐색 예산 소진(가드 중단)", "time": "탐색 시간 소진(가드 중단)",
           "denied": "거부된 호출 반복(가드 중단)",
           "diff not reviewed": "변경 줄을 읽고 판단하지 못함",
           "diff_reviewed missing": "diff_reviewed 필드 누락 또는 불리언 아님",
           "no structured output": "구조화 출력 없음",
           "invalid output": "출력 형식 오류"}


def severity_label(sev) -> str:
    """Korean display label; internal values, schema and state stay English."""
    return _SEV_KO.get(sev, str(sev))


def category_label(cat) -> str:
    return _CAT_KO.get(cat, str(cat))


def state_marker(state: dict) -> str:
    raw = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    return "<!-- %s %s -->" % (MARKER, raw.replace("--", "\\u002d\\u002d"))


def parse_state(body):
    if not body:
        return None
    i = body.rfind(_OPEN)  # the real marker is the footer, always last
    if i < 0:
        return None
    j = body.find(" -->", i)
    if j < 0:
        return None
    try:
        state = json.loads(body[i + len(_OPEN):j])
    except ValueError:
        return None
    return state if isinstance(state, dict) else None


_ISSUE_TAIL = re.compile(r"<!-- ai-review-issue ([0-9A-Za-z_-]+) -->\Z")
_REPLY_TAIL = re.compile(r"<!-- ai-review-reply (\{[^{}]*\}) -->\Z")
MENTION_KEY = re.compile(r"[irv]:\d+\Z")  # also used by validate


def issue_marker(issue_id: str) -> str:
    """Hidden tail of an inline comment: which ledger issue the comment belongs to."""
    return "\n\n<!-- ai-review-issue %s -->" % issue_id


def parse_issue_marker(body):
    """The issue id of a marker at the very end of `body`, else None."""
    m = _ISSUE_TAIL.search(body.rstrip()) if isinstance(body, str) else None
    return m.group(1) if m else None


def reply_marker(key: str) -> str:
    """Hidden tail of a reply: the mention key it answers."""
    return "<!-- ai-review-reply %s -->" % json.dumps({"c": key}, separators=(",", ":"))


def parse_reply(body):
    """The mention key of a reply marker at the very end of `body`, else None."""
    m = _REPLY_TAIL.search(body.rstrip()) if isinstance(body, str) else None
    if not m:
        return None
    try:
        raw = json.loads(m.group(1))
    except ValueError:
        return None
    key = raw.get("c") if isinstance(raw, dict) else None
    return key if isinstance(key, str) and MENTION_KEY.match(key) else None


def _safe(text: str) -> str:
    """Neutralize HTML comment delimiters in LLM-controlled text."""
    return text.replace("<!--", "&lt;!--").replace("-->", "--&gt;")


def render_inline(f: dict) -> str:
    head = "**[%s · 확신 %s · %s]** %s" % (severity_label(f["severity"]), f["confidence"],
                                          category_label(f["category"]), f["title"])
    if f.get("needs_check"):
        head += " ⚠️ 확인 필요"
    out = head + "\n\n"
    if f.get("near_closed"):
        out += "(해결/면제된 이슈 %s 근처의 새 지적)\n\n" % f["near_closed"]
    out += f["detail"]
    if f.get("suggestion"):
        out += "\n\n**제안**\n" + f["suggestion"]
    return _safe(out)


def _paths(paths) -> str:
    return ", ".join("`%s`" % p for p in paths)


def _reason(review: dict) -> str:
    if review["event"] == "REQUEST_CHANGES":
        prev = [p for p in review.get("previous", [])
                if p.get("blocking") and p.get("status") == "open"]
        blocking = [f for f in review["findings"] if f.get("blocking")] + prev
        cnt = {s: sum(1 for f in blocking if f["severity"] == s) for s in ("critical", "major")}
        text = "머지 차단 %d건 (%s %d, %s %d)" % (len(blocking), severity_label("critical"),
                                               cnt["critical"], severity_label("major"), cnt["major"])
        return text + (" · 이전 미해결 %d건 포함" % len(prev) if prev else "")
    if review.get("failed_all"):
        return "AI 리뷰 실패"
    if review.get("no_targets"):
        return "리뷰 대상 변경 없음"
    if review.get("unclear_blocking"):
        return "이전 차단 지적 확인 필요"
    if review.get("incomplete"):
        return "리뷰 미완료 조각 있음"
    # only the gaps that count (`aggregate.merge`): a recheck-only file does not
    gaps = review.get("gaps")
    if any(gaps is None or p in gaps for p in review.get("skipped") or ()):
        return "생략된 파일 있음 (조각 수 한도)"
    if any(gaps is None or p in gaps for p in review.get("truncated") or ()):
        return "잘린 변경 있음 (조각 크기 한도)"
    if review.get("cut"):
        return "줄이 너무 길어 일부만 검토한 파일: " + _paths(review["cut"])
    return "차단 사유 없음"


def _head(review: dict) -> str:
    rows = ["| 심각도 | 건수 |", "|---|---|"]
    for s in _SEVS:
        n = sum(1 for f in review["findings"] if f["severity"] == s)
        rows.append("| %s | %d |" % (severity_label(s), n))
    return "## 🤖 AI Code Review — %s\n**판정 근거:** %s\n\n%s" % (
        _HEADS[review["event"]], _reason(review), "\n".join(rows))


def _cause_label(error: str) -> str:
    """Korean label for a runner error code; unknown text passes through."""
    main, sep, fb = error.partition(" → ")
    if not sep:
        return _CAUSES.get(main, main)
    step = "결론 단계 생략" if fb == "deadline" else "결론 단계 실패"
    return "%s → %s (%s)" % (_CAUSES.get(main, main), step, _CAUSES.get(fb, fb))


def _folded_section(folded: list, shown: int = FOLDED_MAX) -> str:
    """The folded findings, most severe first: `shown` entries (at most FOLDED_MAX, at least
    one) and a line for the rest."""
    ordered = sorted(folded, key=lambda f: (SEV_RANK.get(f.get("severity"), len(SEV_RANK)),
                                            -f.get("confidence", 0), f["file"], f["line"]))
    shown = max(1, min(shown, FOLDED_MAX))
    lines = []
    for f in ordered[:shown]:
        dup = f["dup_of"]
        where = dup if dup.startswith("이번 실행") else "이슈 " + dup
        detail = " ".join((f.get("detail") or "").split())[:FOLDED_DETAIL]
        lines.append("- `%s:%s` %s %s → %s: %s" % (f["file"], f["line"],
                                                  severity_label(f.get("severity")),
                                                  f.get("title") or "(제목 생략)", where, detail))
    if len(ordered) > shown:
        lines.append("외 %d건 (로그 참조)" % (len(ordered) - shown))
    return "%s (%d건)\n%s" % (_FOLDED_HEAD, len(ordered), "\n".join(lines))


def _sections(review: dict, body_only: list, meta: dict, state: dict = None,
              short_prev: bool = False, folded_shown: int = FOLDED_MAX) -> list:
    secs = []
    prev = review.get("previous") or []
    if prev:
        lines = ["- %s `%s:%s` %s%s" % (_PREV.get(p.get("status"), "❔ 불명확"), p["file"], p["line"],
                                       p["title"] or "(제목 생략)", " — " + p["note"] if p.get("note") else "")
                 for p in prev]
        if short_prev:
            lines = ["외 %d건" % len(prev)]
        secs.append(_PREV_HEAD + "\n" + "\n".join(lines or ["- 없음"]))
    if review.get("waived_now"):
        secs.append("면제 %d건" % review["waived_now"])
    plain = [d for d in review.get("duplicates") or [] if not d.get("folded")]
    if plain:
        secs.append("기존 이슈와 중복되어 생략 %d건" % len(plain))
    if review.get("folded"):
        secs.append(_folded_section(review["folded"], folded_shown))
    if (state or {}).get("dropped_closed"):
        secs.append("오래된 종료 이슈 기록 %d건 삭제" % state["dropped_closed"])
    check = [f for f in review["findings"] if f.get("needs_check")]
    if check:
        secs.append("### 확인 필요 (confidence < 80)\n" + "\n".join(
            "- `%s:%s` %s%s" % (f["file"], f["line"], f["title"],
                                " (이슈 %s 근처)" % f["near_closed"] if f.get("near_closed") else "")
            for f in check))
    if body_only:
        secs.append("### inline으로 달 수 없는 지적\n" + "\n\n".join(
            "`%s:%s`\n%s" % (f["file"], f["line"], render_inline(f)) for f in body_only))
    if review.get("summaries"):
        secs.append("### 조각별 요약\n" + "\n\n".join(
            "- " + s for s in review["summaries"]))
    if review.get("errors"):
        secs.append("### 미완료/실패 원인\n" + "\n".join(
            "- c%d: %s" % (i + 1, _cause_label(e)) for i, e in review["errors"]))
    scope = "리뷰 %d files · 생략 %d files" % (len(meta.get("reviewed", [])), len(meta.get("skipped", [])))
    if meta.get("skipped"):
        scope += " (%s)" % ", ".join("`%s`" % p for p in meta["skipped"])
    if review.get("truncated"):
        scope += " · 잘림 %d files (%s)" % (len(review["truncated"]), ", ".join(
            "`%s`" % p for p in review["truncated"]))
    if review.get("cut"):
        scope += " · 줄 잘림 %d files (%s)" % (len(review["cut"]), _paths(review["cut"]))
    if review.get("incomplete"):
        scope += " · 미완료 조각 " + ", ".join(str(i + 1) for i in review["incomplete"])
    secs.append("### 리뷰 범위\n" + scope)
    return secs


def render_body(review: dict, body_only: list, meta: dict, state: dict) -> str:
    mins, secs = divmod(int(round(meta["duration_s"])), 60)
    footer = "<sub>%s · %s chunks · %dm%02ds · %s</sub>\n%s" % (
        meta["model"], meta["chunks"], mins, secs, meta["range"], state_marker(state))

    def build(**kw):
        return [_safe(p) for p in [_head(review)] + _sections(review, body_only, meta, state, **kw)]
    parts = build()
    body = "\n\n".join(parts) + "\n\n" + footer
    if len(body) <= MAX_BODY:
        return body
    # over the limit: the previous-status section goes first, the folded section last
    parts = build(short_prev=True)
    body = "\n\n".join(parts) + "\n\n" + footer
    if len(body) <= MAX_BODY:
        return body
    # Space priority: (a) everything before the folded section's extras that is not "late"
    # (new findings without an inline comment included), (b) the folded minimum, (c) more
    # folded entries, (d) the late sections (summaries, errors, scope).
    budget = MAX_BODY - len(footer) - len(_CUT) - 2
    fi = next((i for i, p in enumerate(parts) if p.startswith(_FOLDED_HEAD)), None)
    late = {i for i, p in enumerate(parts) if p.startswith(_LATE_HEADS)}
    reserve = len(build(short_prev=True, folded_shown=1)[fi]) + 2 if fi is not None else 0
    kept: dict = {}

    def room() -> int:
        return budget - sum(len(t) for t in kept.values()) - 2 * len(kept)
    cut = False
    for i, p in enumerate(parts):
        if i == fi or i in late or cut:
            continue
        r = room() - (reserve if fi is not None else 0)
        if len(p) <= r:
            kept[i] = p
        else:
            if r > 100:
                kept[i] = p[:r]
            cut = True
    if fi is not None:
        r = room()
        for k in range(FOLDED_MAX, 0, -1):
            kept[fi] = build(short_prev=True, folded_shown=k)[fi]
            if len(kept[fi]) <= r:
                break  # k == 1 is kept even over budget: header, one entry and the rest line
    cut = False
    for i in sorted(late):
        if cut:
            break
        r = room()
        if len(parts[i]) <= r:
            kept[i] = parts[i]
        else:
            if r > 100:
                kept[i] = parts[i][:r]
            cut = True
    return "\n\n".join(kept[i] for i in sorted(kept)) + _CUT + "\n\n" + footer


def _cell(text) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def render_step_summary(results: list) -> str:
    rows = ["| chunk | status | turns | wall | LLM | tools | tool calls | denied | tokens in/out |",
            "|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(results, key=lambda x: x["index"]):
        s = r.get("stats") or {}
        status = r["status"] + (" (%s)" % _cell(r["error"]) if r.get("error") else "")
        rows.append("| %d | %s | %s | %.1fs | %.1fs | %.1fs | %s | %s | %s/%s |" % (
            r["index"] + 1, status, s.get("turns", 0), s.get("wall_s", 0), s.get("llm_s", 0),
            s.get("tool_s", 0), s.get("tool_calls", 0), s.get("denied", 0),
            s.get("in_tokens", 0), s.get("out_tokens", 0)))
    return "\n".join(rows) + "\n"
