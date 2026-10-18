"""Posting the review, and the best-effort failure comment for unexpected errors."""
from __future__ import annotations

import sys
import traceback

from review.github_api import GitHubError
from review.render import _safe

DISMISS_MSG = "새 커밋을 다시 리뷰하여 이전 AI 변경 요청을 해제합니다."
MSG_MAX = 200


def log(msg: str) -> None:
    print("[main] " + msg, flush=True)


def post(gh, env, head, review, body, comments, fallback) -> int:
    """Post first, then lift earlier change requests only on an APPROVE verdict."""
    try:
        posted = gh.post_with_fallback(head, review["event"], body, comments, fallback)
    except GitHubError as e:
        log("리뷰 게시 실패: %s" % e)
        return 1
    log("리뷰 게시 완료: id=%s" % (posted or {}).get("id"))
    rc = 1 if review["failed_all"] else 0
    if review["event"] == "APPROVE":
        try:
            n = gh.dismiss_change_requests(env["AI_REVIEW_BOT_NAME"], DISMISS_MSG)
            log("이전 변경 요청 %d건 해제" % n)
        except GitHubError as e:
            log("이전 변경 요청 해제 실패: %s" % e)
            rc = 1
    return rc


def failure_body(exc: BaseException) -> str:
    """COMMENT body for an unexpected failure; deliberately carries no state marker."""
    cause = "%s: %s" % (type(exc).__name__, str(exc)[:MSG_MAX])
    return _safe("## 🤖 AI Code Review — 💬 코멘트\n**판정 근거:** AI 리뷰 실패 (%s)" % cause)


def report_failure(gh, head, exc: BaseException) -> int:
    """Print the traceback; when online, best-effort post the failure COMMENT. Returns 1."""
    traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
    sys.stderr.flush()
    if gh is not None and head:
        try:
            gh.post_review(head, "COMMENT", failure_body(exc), [])
            log("실패 코멘트 게시 완료")
        except Exception as e:  # best effort: the run already failed
            log("실패 코멘트 게시 실패: %s" % e)
    return 1
