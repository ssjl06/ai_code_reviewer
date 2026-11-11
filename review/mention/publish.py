"""Publishing one mention's work: the mention review and the reply."""
from __future__ import annotations

import time
from typing import Optional

from review import scope as sc
from review.github_api import GitHubError, PostUnknown, retry_post
from review.mention import body
from review.mention.ledger import apply_changes, ledger_event, marker_state
from review.render import MAX_BODY, parse_reply, state_marker
from review.state_fit import MARKER_BUDGET
from review.validate import normalize_state


DISMISS_MSG = "코멘트를 반영하여 이전 AI 변경 요청을 해제합니다."
HEAD_MOVED = "head가 바뀌어 언급 처리를 중단함"
UNKNOWN = "리뷰 게시 결과를 확인할 수 없어 언급 처리를 중단함"
LEDGER_FULL = "장부가 가득 차 이슈로 등록하지 않음"
TOO_BIG = "언급 리뷰가 크기 제한을 넘어 게시하지 않음 (다음 실행에서 다시)"


def wait_head(ph, pr: dict) -> dict:
    """`pr`, read again (up to `mention_head_tries` reads in all, `mention_head_delay_s`
    apart) while the API head is not the phase's head and that head is a commit this phase
    pushed: the API may not show the push yet."""
    for _ in range(ph.cfg["mention_head_tries"] - 1):
        if pr["head"]["sha"] == ph.head or ph.head not in ph.pushed:
            break
        time.sleep(ph.cfg["mention_head_delay_s"])
        pr = ph.gh.get_pr()
    return pr


def _note(n: int, msg: str) -> None:
    print("[m%d] %s" % (n, msg), flush=True)


def _stored(listing, bot: str, key: str):
    def find() -> Optional[dict]:
        for c in listing():
            login = ((c.get("user") or {}).get("login")) or ""
            if login.lower() == bot.lower() and parse_reply(c.get("body")) == key:
                return c
        return None
    return find


def reply(ph, n: int, m: dict, text: str, summary: str) -> str:
    """Post the reply that answers `m`: in its thread for an inline mention (refused with a
    4xx: once as a PR comment), else as a PR comment quoting it. 'answered' or 'failed'."""
    gh, key, link = ph.gh, m["key"], m.get("url") or ""

    def as_comment() -> dict:
        b = body.reply(text, summary, key, m.get("body") or "", link)
        return retry_post(lambda: gh.post_issue_comment(b),
                          _stored(gh.list_issue_comments, ph.bot, key))
    try:
        if m["kind"] in ("inline-new", "inline-reply"):
            b = body.reply(text, summary, key, None, link)
            try:
                c = retry_post(lambda: gh.reply_review_comment(m["root_id"], b),
                               _stored(gh.list_review_comments, ph.bot, key))
            except GitHubError as e:
                if not 400 <= e.status < 500:
                    raise
                _note(n, "스레드 답글 거부됨 (%s) — PR 코멘트로 다시 시도" % e)
                c = as_comment()
        else:
            c = as_comment()
    except GitHubError as e:
        _note(n, "답글 게시 실패: %s" % e)
        return "failed"
    _note(n, "답글 게시 완료: %s" % (c or {}).get("html_url"))
    return "answered"


def _draft(ph, m: dict, out: dict, plan: dict, new: dict, effect: dict,
           guide: Optional[dict], base: str, summary: str) -> dict:
    """The mention review for `new`: its state, event, bodies and inline comments, and
    whether the marker and both bodies fit their limits."""
    rec = {"k": m["key"], "w": effect["w"], "n": effect["n"], "up": effect["up"]}
    g = body.guideline_receipt(guide)
    if g:
        rec["g"] = g
    marker = marker_state(new, rec)
    nst = normalize_state(marker)
    current = sc.determine(ph.repo, base, ph.head, nst, ph.cfg)["mode"] == "unchanged"
    blocking = {f["id"] for f in plan["findings"] if f.get("blocking")}
    added_blocking = bool(effect["up"]) or any(i in blocking for i in effect["n"])
    event = ledger_event(nst, current, added_blocking)
    findings = [f for f in plan["findings"] if f["id"] in effect["n"]]
    comments = [{"path": f["file"], "line": f["line"], "side": "RIGHT",
                 "body": body.inline_body(f)} for f in findings]
    text, fallback = body.mention_review(m["author"], m.get("url") or "", out["reply"], marker,
                                         event, summary, tuple(findings))
    fits = len(state_marker(marker)) <= MARKER_BUDGET and \
        max(len(text), len(fallback)) <= MAX_BODY
    return {"state": nst, "event": event, "text": text, "fallback": fallback,
            "comments": comments, "fits": fits}


def mention_review(ph, n: int, m: dict, out: dict, plan: dict, new: dict, effect: dict,
                   guide: Optional[dict], base: str) -> dict:
    """Post the mention review on the head (6.2). `{"status", "summary"}`: status "posted"
    (its state is the phase's state now), "skipped" (nothing left to store once the new
    issues that did not fit were dropped), "failed" (refused, too big, or the dismissal
    after an approval failed: the mention stays unanswered), "unknown" (the POST may have
    landed: the phase stops) or "moved" (the API head is not the phase's head)."""
    summary = body.summary(effect, plan, guide, ph.bot)
    d = _draft(ph, m, out, plan, new, effect, guide, base, summary)
    if not d["fits"] and effect["n"]:
        _note(n, LEDGER_FULL)
        plan = dict(plan, notes=plan["notes"] + [LEDGER_FULL],
                    changes=dict(plan["changes"], n=[]))
        new, effect = apply_changes(ph.state, plan["changes"])
        summary = body.summary(effect, plan, guide, ph.bot)
        if not any(effect.values()):
            return {"status": "skipped", "summary": summary}
        d = _draft(ph, m, out, plan, new, effect, guide, base, summary)
    if not d["fits"]:
        _note(n, TOO_BIG)
        return {"status": "failed", "summary": summary}
    if wait_head(ph, ph.gh.get_pr())["head"]["sha"] != ph.head:
        _note(n, HEAD_MOVED)  # a review on a commit the API does not show is refused
        return {"status": "moved", "summary": summary}
    try:
        posted = retry_post(
            lambda: ph.gh.post_with_fallback(ph.head, d["event"], d["text"], d["comments"],
                                             d["fallback"]),
            lambda: ph.gh.mention_receipt(ph.bot, m["key"]))
    except PostUnknown as e:
        _note(n, "언급 리뷰 게시 실패: %s" % e)
        _note(n, UNKNOWN)
        return {"status": "unknown", "summary": summary}
    except GitHubError as e:
        _note(n, "언급 리뷰 게시 실패: %s" % e)
        return {"status": "failed", "summary": summary}
    ph.state = d["state"]
    _note(n, "언급 리뷰 게시 완료: %s %s" % (d["event"],
                                       posted.get("html_url") or posted.get("url")))
    if d["event"] == "APPROVE" and not dismiss(ph, n):
        return {"status": "failed", "summary": summary}  # the next run dismisses, then replies
    return {"status": "posted", "summary": summary}


def dismiss(ph, n: int) -> bool:
    """Dismiss the bot's change requests after an approval; False when that failed."""
    try:
        _note(n, "이전 변경 요청 %d건 해제" % ph.gh.dismiss_change_requests(ph.bot, DISMISS_MSG))
    except GitHubError as e:
        _note(n, "이전 변경 요청 해제 실패: %s" % e)
        return False
    return True
