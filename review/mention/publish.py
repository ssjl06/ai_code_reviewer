"""Publishing one mention's work: the mention review and the reply."""
from __future__ import annotations

from typing import Optional

from review import scope as sc
from review.failure import DISMISS_MSG
from review.github_api import GitHubError, retry_post
from review.mention import body
from review.mention.ledger import ledger_event, marker_state
from review.render import parse_reply
from review.validate import normalize_state


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


def mention_review(ph, n: int, m: dict, out: dict, plan: dict, new: dict, effect: dict,
                   guide: Optional[dict], base: str, summary: str) -> Optional[dict]:
    """Post the mention review on the head (6.2); the posted review, or None when it failed.
    On success its state becomes the phase's state."""
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
    try:
        posted = retry_post(
            lambda: ph.gh.post_with_fallback(ph.head, event, text, comments, fallback),
            lambda: ph.gh.mention_receipt(ph.bot, m["key"]))
    except GitHubError as e:
        _note(n, "언급 리뷰 게시 실패: %s" % e)
        return None
    ph.state = nst
    _note(n, "언급 리뷰 게시 완료: %s %s" % (event, posted.get("html_url") or posted.get("url")))
    if event == "APPROVE":
        try:
            _note(n, "이전 변경 요청 %d건 해제" % ph.gh.dismiss_change_requests(ph.bot, DISMISS_MSG))
        except GitHubError as e:
            _note(n, "이전 변경 요청 해제 실패: %s" % e)
            ph.failed = True
    return posted
