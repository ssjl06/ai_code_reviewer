"""What an earlier run of a mention left (spec 6.4): its mention review (the receipt `m.k`),
whose approval may still owe the dismissals, and its guideline commit (the trailer)."""
from __future__ import annotations

from typing import Optional

from review import rules_git
from review.mention import body, publish
from review.mention.apply import RULE_OPS


def _note(n: int, msg: str) -> None:
    print("[m%d] %s" % (n, msg), flush=True)


def from_receipt(ph, n: int, m: dict, receipt: dict) -> str:
    """Answer `m` from the mention review an earlier run posted. When that review is an
    approval and still the bot's latest review, the change requests it lifts are dismissed
    first (again: dismissing is idempotent); a failure leaves `m` unanswered. A later bot
    review decides the verdict now, so nothing is dismissed for an older approval."""
    _note(n, "이전 실행의 언급 리뷰 발견: %s" % receipt.get("url"))
    if receipt.get("state") == "APPROVED":
        mine = ph.gh.bot_reviews(ph.bot)
        if mine and mine[0].get("id") == receipt.get("id") and not publish.dismiss(ph, n):
            return "failed"
    text, _, summary = body.summary_from_receipt(receipt).partition("\n")
    return publish.reply(ph, n, m, text, summary)


def committed(ph, m: dict, base: str) -> Optional[str]:
    """The subject of the guideline commit an earlier run pushed for `m` (its trailer), for
    a mention with a guideline command; else None."""
    if m.get("command") not in RULE_OPS:
        return None
    return rules_git.receipt(ph.repo, base, ph.head, m["key"])
