"""Korean texts of a mention: the applied-summary, the mention review body and the reply.

`guideline` (argument of `summary`) is the result dict of the guideline step, or None:
  status:  "added" | "updated" | "removed" | "unchanged" | "failed"
  path, rule_id: the guideline file and the rule id
  old:     the old rule text (update, remove), else None
  pushed:  True when the commit reached the PR branch
  reason:  Korean text, when failed
  entry:   the entry text to commit by hand, when failed

Every text passes `render._safe`; a marker is appended last and never escaped.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from review.prompt import _fence
from review.render import (MAX_BODY, _HEADS, _safe, issue_marker, render_inline, reply_marker,
                           state_marker)

_OPS = {"added": "추가", "updated": "수정", "removed": "삭제"}
RECOVERED = "이전 실행에서 반영했습니다."
PULL_LINE = "- 브랜치에 커밋을 추가했습니다. 다음 push 전에 pull 하세요."
_HINTS = {"withdraw": "반영하려면 `@%s /철회`로 불러 주세요.",
          "rule": "지침으로 남기려면 `@%s /지침 <내용>`으로 불러 주세요."}
_QUOTE_MAX = 200


def _line(text: str) -> str:
    return " ".join((text or "").split())


def guideline_receipt(guideline: Optional[dict]) -> Optional[str]:
    """The receipt `g` of a change that was committed: `<path> <rule id> 추가|수정|삭제`."""
    if guideline and guideline.get("status") in _OPS:
        return "%s %s %s" % (guideline["path"], guideline["rule_id"], _OPS[guideline["status"]])
    return None


def _joined(parts: List[str]) -> str:
    return "**반영:** " + (" · ".join(parts) if parts else "없음")


def summary(effect: dict, plan: dict, guideline: Optional[dict], bot: str) -> str:
    """The summary of 5.4 from what `apply_changes` reported (`effect`) and the `plan`."""
    blocking = {f["id"] for f in plan.get("findings", ()) if f.get("blocking")}
    parts = ["철회 " + i for i in effect.get("w", ())]
    parts += ["새 이슈 %s%s" % (i, " (차단)" if i in blocking else "") for i in effect.get("n", ())]
    parts += ["차단 상향 " + i for i in effect.get("up", ())]
    parts += ["철회 %s (이미 반영됨)" % i for i in plan.get("done", ())]
    lines: List[str] = []
    g = guideline or {}
    status = g.get("status")
    if status in _OPS:
        parts.append("지침 %s %s %s" % (g["path"], g["rule_id"], _OPS[status]))
        if status in ("updated", "removed") and g.get("old"):
            lines.append("- 이전 지침 내용: \"%s\"" % _line(g["old"]))
        if g.get("pushed"):
            lines.append(PULL_LINE)
    elif status == "unchanged":
        parts.append("지침 %s %s (이미 반영됨)" % (g["path"], g["rule_id"]))
    elif status == "failed":
        lines.append("- 반영 안 됨: 지침 %s" % _line(g.get("reason", "")))
        if g.get("entry"):
            fence = _fence(g["entry"])
            lines.append("  직접 추가할 내용:\n%s\n%s\n%s" % (fence, g["entry"], fence))
    lines = ["- 반영 안 됨: " + r for r in plan.get("dropped", ())] + lines
    lines += ["- " + n for n in plan.get("notes", ())]
    if plan.get("hint") in _HINTS:
        lines.append(_HINTS[plan["hint"]] % bot)
    return "\n".join([_joined(parts)] + lines)


def summary_from_receipt(receipt: dict) -> str:
    """The same format from the stored receipt `m` (6.4): the work of an earlier run."""
    m = receipt.get("m") or {}
    parts = ["철회 " + i for i in m.get("w", ())]
    parts += ["새 이슈 " + i for i in m.get("n", ())]
    parts += ["차단 상향 " + i for i in m.get("up", ())]
    if m.get("g"):
        parts.append("지침 " + m["g"])
    head = RECOVERED
    if receipt.get("url"):
        head += " (%s)" % receipt["url"]
    return head + "\n" + _joined(parts)


def inline_body(f: dict) -> str:
    """The body of the inline comment of a new issue: the finding and its hidden id."""
    return render_inline(f) + issue_marker(f["id"])


def _reason(event: str, state: dict) -> str:
    unclear = state.get("unclear") or []
    blocking = [i for i in state["issues"] if i.get("status", "open") == "open"
                and i.get("blocking") and i["id"] not in unclear]
    if event == "REQUEST_CHANGES":
        return "머지 차단 %d건" % len(blocking)
    if event == "APPROVE":
        return "차단 사유 없음"
    return "이 코멘트 반영만으로는 승인 조건을 충족하지 않음"


def _listed(findings: List[dict]) -> str:
    rows = ["- `%s:%s` %s" % (f["file"], f["line"], _line(f["title"])) for f in findings]
    return "### 새 이슈\n" + "\n".join(rows)


def mention_review(author: str, url: str, answer: str, state: dict, event: str, summary_text: str,
                   findings: Tuple[dict, ...] = ()) -> Tuple[str, str]:
    """`(body, fallback)` of the mention review (6.2). The fallback is for a 422 on the
    inline comments: it lists `findings` (full text, no issue marker). `state` is the state
    to put in the marker."""
    head = "## 🤖 AI Code Review — %s\n**판정 근거:** %s\n\n@%s의 코멘트(%s)를 반영한 리뷰" % (
        _HEADS[event], _reason(event, state), author, url)
    opened = [i for i in state["issues"]
              if i.get("status", "open") == "open" and i.get("blocking")]
    blocking = ["### 열린 차단 이슈\n" + "\n".join(
        "- `%s` `%s:%s` %s" % (i["id"], i["file"], i["line"], _line(i.get("title", "")))
        for i in opened)] if opened else []
    marker = state_marker(state)

    def build(extra: List[str]) -> str:
        room = MAX_BODY - len(marker) - 2
        parts = [head, "### 답변\n" + answer, summary_text] + extra + blocking
        text = _safe("\n\n".join(parts))
        return (text[:room] if len(text) > room else text) + "\n\n" + marker
    full = [render_inline(f) for f in findings]
    return build([]), build([_listed(findings)] + full if findings else [])


def reply(text: str, summary_text: str, key: str, quote_line: Optional[str], link: str) -> str:
    """The reply body: the quote of the mention (issue comments only), the answer, the summary,
    the reply marker last."""
    parts = []
    if quote_line is not None:
        first = _line((quote_line or "").split("\n", 1)[0])[:_QUOTE_MAX]
        parts.append("> %s ([원문](%s))" % (first, link))
    parts += [text, summary_text]
    marker = reply_marker(key)
    text = _safe("\n\n".join(parts))
    room = MAX_BODY - len(marker) - 2
    return (text[:room] if len(text) > room else text) + "\n\n" + marker
