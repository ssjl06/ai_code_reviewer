"""One mention, in the order of spec 6.4: head check, receipt, time, the replies that need no
session, session, plan, guideline push, mention review, reply. Only the reply answers it."""
from __future__ import annotations

import time
from typing import List, Optional

from review.claude_cmd import MENTION
from review.claude_runner import reask_korean, run_chunk
from review.diff_split import line_map, parse_unified_diff, reviewable
from review.gitutil import blob_id, diff_text, read_blobs
from review.ledger import CLOSED
from review.mention import body, context, fetch, guideline, publish
from review.mention.apply import plan_actions
from review.mention.command import mentions_bot
from review.mention.ledger import apply_changes
from review.previous import anchor_of
from review.render import parse_reply

MIN_LEFT_S = 60  # a mention is started only with this much left beyond the publish reserve
TIME_REPLY = "시간이 부족해 처리하지 못했습니다. 다시 불러 주세요."
UNKNOWN_REPLY = "알 수 없는 명령입니다. 쓸 수 있는 명령: `/철회`, `/지침`, `/지침수정`, `/지침삭제`"
HEAD_MOVED = "head가 바뀌어 언급 처리를 중단함"
NO_OUTPUT = "처리하지 못했습니다: %s"
INLINE = ("inline-new", "inline-reply")


class Phase:
    """What the mentions of one phase share. `state` and `head` are replaced as mentions
    publish (single writer, spec 6.1); `failed` records a failure besides the replies."""

    def __init__(self, repo, env, cfg, scfg, gh, bot, paths, deadline, head, state, lists,
                 callers):
        self.repo, self.env, self.cfg, self.scfg, self.gh = repo, env, cfg, scfg, gh
        self.bot, self.paths, self.deadline, self.head, self.state = bot, paths, deadline, head, state
        self.issue_comments, self.review_comments, self.reviews = lists
        self.callers = {c.lower() for c in callers}  # allowed authors of pending mentions
        self.failed = False

    def end(self) -> float:
        """The monotonic deadline of sessions and resumes: the phase's minus the reserve."""
        return self.deadline - self.cfg["mention_publish_reserve_s"]


def note(n: int, msg: str) -> None:
    print("[m%d] %s" % (n, msg), flush=True)


def _closed(pr: dict) -> bool:
    return pr.get("state", "open") != "open" or bool(pr.get("merged"))


def handle(ph: Phase, n: int, m: dict) -> str:
    """'answered', 'failed' or 'stopped' (the head moved: the phase stops)."""
    note(n, "언급 %s (%s, @%s) 명령: %s" % (m["key"], m["kind"], m["author"],
                                          m.get("command") or "없음"))
    nothing = body.summary({}, {}, None, ph.bot)
    pr = ph.gh.get_pr()
    if pr["head"]["sha"] != ph.head:
        note(n, HEAD_MOVED)
        return "stopped"
    receipt = ph.gh.mention_receipt(ph.bot, m["key"])
    if receipt:
        note(n, "이전 실행의 언급 리뷰 발견: %s" % receipt.get("url"))
        text, _, summary = body.summary_from_receipt(receipt).partition("\n")
        return publish.reply(ph, n, m, text, summary)
    if ph.deadline - time.monotonic() < ph.cfg["mention_publish_reserve_s"] + MIN_LEFT_S:
        note(n, "시간 부족 — 처리하지 않음")
        return publish.reply(ph, n, m, TIME_REPLY, nothing)
    issues = ph.state["issues"] if ph.state else []
    thread = fetch.thread(ph.review_comments, m["root_id"]) if m["kind"] in INLINE else []
    targets = context.targets(m, thread, issues, ph.bot)
    if m.get("command") == "unknown":
        return publish.reply(ph, n, m, UNKNOWN_REPLY, nothing)
    if m.get("command") == "withdraw":
        if not targets:
            note(n, "철회 대상 없음")
            return publish.reply(ph, n, m, context.no_target_reply(issues, ph.bot), nothing)
        note(n, "철회 대상: " + ", ".join(t["id"] for t in targets))
    base = pr["base"]["sha"]
    sess = _session(ph, n, m, pr, base, targets, thread)
    out = sess["r"].get("output")
    if not out:
        cause = str(sess["r"].get("error") or "출력 없음")[:200]
        note(n, "세션 출력 없음: %s" % cause)
        return publish.reply(ph, n, m, NO_OUTPUT % cause, nothing)
    if _closed(pr):
        note(n, "PR이 닫혀 답변만 함")
        return publish.reply(ph, n, m, out["reply"], nothing)
    plan = _plan(ph, m, out, targets, base)
    guide = guideline.step(ph, n, m, pr, base, plan, sess)
    effect = {"w": [], "n": [], "up": []}
    new = None
    if ph.state is not None:
        new, effect = apply_changes(ph.state, plan["changes"])
    for line in _plan_lines(plan, effect):
        note(n, line)
    summary = body.summary(effect, plan, guide, ph.bot)
    if any(effect.values()):
        if publish.mention_review(ph, n, m, out, plan, new, effect, guide, base, summary) is None:
            return "failed"
    return publish.reply(ph, n, m, out["reply"], summary)


def _plan_lines(plan: dict, effect: dict) -> List[str]:
    out = ["반영: 철회 %s" % i for i in effect["w"]]
    out += ["반영: 새 이슈 %s" % i for i in effect["n"]]
    out += ["반영: 차단 상향 %s" % i for i in effect["up"]]
    out += ["반영 안 됨: %s" % r for r in plan["dropped"]] + ["참고: %s" % x for x in plan["notes"]]
    return out


def _item(c: dict) -> dict:
    return {"author": ((c.get("user") or {}).get("login")) or "", "body": c.get("body") or "",
            "created_at": c.get("created_at") or c.get("submitted_at") or ""}


def _talk(ph: Phase, m: dict) -> List[dict]:
    """The PR's earlier mentions by allowed callers and the bot's replies, oldest first (a
    general mention's context)."""
    me = ph.bot.lower()
    out = []
    for c in ph.issue_comments + ph.review_comments + ph.reviews:
        it = _item(c)
        if c.get("id") == m["id"] or not it["created_at"] or it["created_at"] > m["created_at"]:
            continue
        mine = it["author"].lower() == me
        allowed = c.get("author_association") in fetch.ALLOWED or \
            it["author"].lower() in ph.callers
        if (mine and parse_reply(it["body"])) or \
                (not mine and allowed and mentions_bot(it["body"], ph.bot)):
            out.append(it)
    return sorted(out, key=lambda it: it["created_at"])


def _session(ph: Phase, n: int, m: dict, pr: dict, base: str, targets, thread) -> dict:
    cfg = ph.cfg
    items = [_item(c) for c in thread if c["id"] != m["id"]] if m["kind"] in INLINE \
        else _talk(ph, m)
    issues = ph.state["issues"] if ph.state else []
    ctx = {"conversation": context.conversation(items, cfg["mention_conversation_chars"]),
           "window": context.code_window(ph.repo, m, base, ph.head, cfg["mention_code_lines"]),
           "targets": targets,
           "open": [i for i in issues if i.get("status", "open") not in CLOSED],
           "guidelines": context.guidelines(ph.repo, m, cfg["mention_rules_chars"]),
           "title": pr.get("title") or "", "base": (pr.get("base") or {}).get("ref") or "",
           "head": (pr.get("head") or {}).get("ref") or "",
           "budget": cfg["mention_explore_budget"]}
    chunk = {"index": n - 1, "files": [], "est_tokens": 0}  # label and dir: m<n>, mention-<n>
    r = run_chunk(chunk, context.build_prompt(m, ctx), None, ph.scfg, ph.paths, ph.env,
                  deadline=ph.end(), kind=MENTION)
    r = reask_korean(r, chunk, ph.scfg, ph.paths, ph.env, MENTION, ph.end())
    return {"r": r, "chunk": chunk}


def _plan(ph: Phase, m: dict, out: dict, targets, base: str) -> dict:
    rev = [f for f in parse_unified_diff(diff_text(ph.repo, base, ph.head, merge_base=True))
           if reviewable(f, ph.cfg)]
    covered = {f["path"] for f in rev}
    st: Optional[dict] = ph.state

    def same_line(path: str) -> bool:
        return st["sha"] == ph.head or \
            blob_id(ph.repo, st["sha"], path) == blob_id(ph.repo, ph.head, path)
    anchor = anchor_of(read_blobs(ph.repo, ph.head, sorted(covered)))
    return plan_actions(out, m, targets, st, line_map(rev, "right_lines"), covered, same_line,
                        anchor, ph.cfg)
