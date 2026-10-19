"""Render the PR review: Korean body, inline comments, state marker, step summary."""
from __future__ import annotations

import json

MARKER = "ai-review-state"
MAX_BODY = 60000
_OPEN = "<!-- " + MARKER + " "
_HEADS = {"APPROVE": "✅ 승인", "REQUEST_CHANGES": "❌ 변경 요청", "COMMENT": "💬 코멘트"}
_PREV = {"resolved": "✅ 해결", "open": "⏳ 미해결", "unclear": "❔ 불명확"}
_SEVS = ["critical", "major", "minor", "info"]
_CUT = "\n\n(본문 길이 제한으로 일부 생략)"


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


def _safe(text: str) -> str:
    """Neutralize HTML comment delimiters in LLM-controlled text."""
    return text.replace("<!--", "&lt;!--").replace("-->", "--&gt;")


def render_inline(f: dict) -> str:
    head = "**[%s · 확신 %s · %s]** %s" % (f["severity"], f["confidence"], f["category"], f["title"])
    if f.get("needs_check"):
        head += " ⚠️ 확인 필요"
    out = head + "\n\n" + f["detail"]
    if f.get("suggestion"):
        out += "\n\n**제안**\n" + f["suggestion"]
    return _safe(out)


def _reason(review: dict) -> str:
    if review["event"] == "REQUEST_CHANGES":
        prev = [p for p in review.get("previous", [])
                if p.get("blocking") and p.get("status") == "open"]
        blocking = [f for f in review["findings"] if f.get("blocking")] + prev
        cnt = {s: sum(1 for f in blocking if f["severity"] == s) for s in ("critical", "major")}
        text = "머지 차단 %d건 (critical %d, major %d)" % (len(blocking), cnt["critical"], cnt["major"])
        return text + (" · 이전 미해결 %d건 포함" % len(prev) if prev else "")
    if review.get("failed_all"):
        return "AI 리뷰 실패"
    if review.get("no_targets"):
        return "리뷰 대상 변경 없음"
    if review.get("unclear_blocking"):
        return "이전 차단 지적 확인 필요"
    if review.get("incomplete"):
        return "리뷰 미완료 조각 있음"
    if review.get("skipped"):
        return "생략된 파일 있음 (조각 수 한도)"
    if review.get("truncated"):
        return "잘린 변경 있음 (조각 크기 한도)"
    return "차단 사유 없음"


def _head(review: dict) -> str:
    rows = ["| 심각도 | 건수 |", "|---|---|"]
    for s in _SEVS:
        rows.append("| %s | %d |" % (s, sum(1 for f in review["findings"] if f["severity"] == s)))
    return "## 🤖 AI Code Review — %s\n**판정 근거:** %s\n\n%s" % (
        _HEADS[review["event"]], _reason(review), "\n".join(rows))


def _sections(review: dict, body_only: list, meta: dict) -> list:
    secs = []
    prev = review.get("previous") or []
    if prev:
        lines = ["- %s `%s:%s` %s%s" % (_PREV.get(p.get("status"), "❔ 불명확"), p["file"], p["line"],
                                       p["title"], " — " + p["note"] if p.get("note") else "")
                 for p in prev]
        secs.append("### 이전 지적 처리 현황\n" + "\n".join(lines or ["- 없음"]))
    check = [f for f in review["findings"] if f.get("needs_check")]
    if check:
        secs.append("### 확인 필요 (confidence < 80)\n" + "\n".join(
            "- `%s:%s` %s" % (f["file"], f["line"], f["title"]) for f in check))
    if body_only:
        secs.append("### inline으로 달 수 없는 지적\n" + "\n\n".join(
            "`%s:%s`\n%s" % (f["file"], f["line"], render_inline(f)) for f in body_only))
    if review.get("summaries"):
        secs.append("### 조각별 요약\n" + "\n\n".join(
            "- " + s for s in review["summaries"]))
    if review.get("errors"):
        secs.append("### 미완료/실패 원인\n" + "\n".join(
            "- c%d: %s" % (i + 1, e) for i, e in review["errors"]))
    scope = "리뷰 %d files · 생략 %d files" % (len(meta.get("reviewed", [])), len(meta.get("skipped", [])))
    if meta.get("skipped"):
        scope += " (%s)" % ", ".join("`%s`" % p for p in meta["skipped"])
    if review.get("truncated"):
        scope += " · 잘림 %d files (%s)" % (len(review["truncated"]), ", ".join(
            "`%s`" % p for p in review["truncated"]))
    if review.get("incomplete"):
        scope += " · 미완료 조각 " + ", ".join(str(i + 1) for i in review["incomplete"])
    secs.append("### 리뷰 범위\n" + scope)
    return secs


def render_body(review: dict, body_only: list, meta: dict, state: dict) -> str:
    mins, secs = divmod(int(round(meta["duration_s"])), 60)
    footer = "<sub>%s · %s chunks · %dm%02ds · %s</sub>\n%s" % (
        meta["model"], meta["chunks"], mins, secs, meta["range"], state_marker(state))
    parts = [_safe(p) for p in [_head(review)] + _sections(review, body_only, meta)]
    body = "\n\n".join(parts) + "\n\n" + footer
    if len(body) <= MAX_BODY:
        return body
    budget = MAX_BODY - len(footer) - len(_CUT) - 2
    kept, used = [], 0
    for p in parts:
        room = budget - used - (2 if kept else 0)
        if len(p) <= room:
            kept.append(p)
            used += len(p) + (2 if len(kept) > 1 else 0)
            continue
        if room > 100:
            kept.append(p[:room])
        break
    return "\n\n".join(kept) + _CUT + "\n\n" + footer


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
