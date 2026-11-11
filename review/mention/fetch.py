"""The mentions of a PR that still wait for an answer, oldest first."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

from review.mention import command as cmd
from review.render import parse_reply

ALLOWED = ("OWNER", "MEMBER", "COLLABORATOR")
WRITE = ("admin", "maintain", "write")


def _time(text: str) -> datetime:
    t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _login(item: dict) -> str:
    return ((item.get("user") or {}).get("login")) or ""


def _roots(review_comments: List[dict]) -> Dict[int, int]:
    parent = {c["id"]: c.get("in_reply_to_id") for c in review_comments}
    roots = {}
    for cid in parent:
        cur, seen = cid, set()
        while parent.get(cur) and parent[cur] in parent and cur not in seen:
            seen.add(cur)
            cur = parent[cur]
        roots[cid] = parent[cur] if parent.get(cur) else cur
    return roots


def thread(review_comments: List[dict], root_id: int) -> List[dict]:
    roots = _roots(review_comments)
    mine = [c for c in review_comments if roots[c["id"]] == root_id]
    return sorted(mine, key=lambda c: (_time(c["created_at"]), c["id"]))


def pending(issue_comments, review_comments, reviews, bot, since,
            permission: Callable[[str], Optional[str]]) -> Tuple[List[dict], List[str]]:
    """(mentions to answer oldest first, log lines for the ones ignored)."""
    me = bot.lower()
    answered = set()
    for c in list(issue_comments) + list(review_comments):
        if _login(c).lower() == me:
            key = parse_reply(c.get("body") or "")
            if key:
                answered.add(key)
    roots = _roots(review_comments)
    cands = []
    for c in issue_comments:
        cands.append(("i:%d" % c["id"], "general", c["created_at"], c, None))
    for c in review_comments:
        kind = "inline-reply" if c.get("in_reply_to_id") else "inline-new"
        cands.append(("r:%d" % c["id"], kind, c["created_at"], c, roots[c["id"]]))
    for r in reviews:
        cands.append(("v:%d" % r["id"], "review-body", r.get("submitted_at") or "", r, None))
    cands = [x for x in cands if x[2]]
    cands.sort(key=lambda x: (_time(x[2]), x[0]))
    limit = _time(since) if since else None
    asked: Dict[str, Optional[str]] = {}
    out, log = [], []
    for key, kind, when, c, root in cands:
        author, body = _login(c), c.get("body") or ""
        if author.lower() == me or not cmd.mentions_bot(body, bot) or key in answered:
            continue
        if limit and _time(when) < limit:
            log.append("%s: mention_since 이전의 멘션이라 건너뜁니다." % key)
            continue
        if c.get("author_association") not in ALLOWED:
            if author not in asked:
                asked[author] = permission(author)
            if asked[author] not in WRITE:
                log.append("%s: %s 님은 권한이 없어 건너뜁니다." % (key, author))
                continue
        command, ids = cmd.parse(body, bot)
        m = {"key": key, "kind": kind, "id": c["id"], "body": body, "author": author,
             "url": c.get("html_url"), "created_at": when, "command": command, "ids": ids}
        if kind != "general" and kind != "review-body":
            for f in ("path", "side", "line", "start_line", "diff_hunk"):
                m[f] = c.get(f)
            m["root_id"] = root
        out.append(m)
    return out, log
