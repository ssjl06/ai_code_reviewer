"""The guideline step of one mention (spec 7.4, 7.5): commit and push the checked change,
or report the commit an earlier run left (its trailer)."""
from __future__ import annotations

import os
import re
from typing import Optional
from urllib.parse import urlsplit

from review import rules, rules_edit, rules_git
from review.claude_cmd import MENTION
from review.claude_runner import resume_ask
from review.gitutil import git
from review.mention.apply import NO_GUIDELINE, RULE_OPS, WRONG_OP

OVERFLOW_ASK = "같은 디렉토리에 새 주제 파일 경로를 정하라. 경로만 바꾼 최종 JSON을 출력하라."
FORK = "PR 브랜치가 이 저장소에 없어 커밋할 수 없습니다"
TOO_LONG = "지침 파일이 %d줄을 넘게 됩니다" % rules.MAX_LINES
NO_COMMIT = "커밋이 만들어지지 않았습니다"
_DONE = {"add": "added", "update": "updated", "remove": "removed"}
_WORD = {"add": "to", "update": "in", "remove": "from"}
_SUBJECT = re.compile(r"ai-review: (add|update|remove) rule (R-[0-9a-f]{6}) (?:to|in|from) (\S+) "
                      r"\(PR #\d+\)\Z")


def _note(n: int, msg: str) -> None:
    print("[m%d] %s" % (n, msg), flush=True)


def _from_subject(subject: str) -> Optional[dict]:
    hit = _SUBJECT.match(subject)
    if not hit:
        return None
    return {"status": _DONE[hit.group(1)], "path": hit.group(3), "rule_id": hit.group(2),
            "old": None, "pushed": True}


def _old_rule(repo: str, change: dict) -> Optional[str]:
    if change["op"] == "add" or not os.path.isfile(rules.abs_path(repo, change["path"])):
        return None
    doc = rules.read_doc(repo, change["path"]) or {"rules": []}
    return next((r["text"] for r in doc["rules"] if r["id"] == change.get("target")), None)


def _overflows(repo: str, change: dict, source: str) -> bool:
    """An `add` that would grow its existing file past the line limit (7.4)."""
    if not os.path.isfile(rules.abs_path(repo, change["path"])):
        return False
    doc = rules.read_doc(repo, change["path"])
    if doc is None:
        return False
    entry = {"id": rules.rule_id(change["path"], change["rule"]), "text": change["rule"],
             "reason": change["reason"], "source": source}
    grown = rules.render_doc(dict(doc, rules=doc["rules"] + [entry])).count("\n")
    return grown > rules.MAX_LINES and grown > rules.render_doc(doc).count("\n")


def _new_path(ph, n: int, sess: dict, path: str) -> str:
    """One resume without tools for a new topic file in the same directory, else `-N`."""
    alt = resume_ask(sess["r"], sess["chunk"], OVERFLOW_ASK, ph.scfg, ph.paths, ph.env, MENTION,
                     ph.end())
    cand = ((alt or {}).get("guideline") or {}).get("path")
    if not isinstance(cand, str) or rules.check_path(cand) \
            or os.path.dirname(cand) != os.path.dirname(path):
        cand = path
    new = rules_edit.free_path(ph.repo, cand)
    _note(n, "지침 파일이 길어 새 파일에 씀: %s" % new)
    return new


def _entry(rid: str, change: dict, source: str) -> Optional[str]:
    if change["op"] == "remove":
        return None
    return "## %s  %s\n- 이유: %s\n- 출처: %s" % (rid, change["rule"], change["reason"], source)


def _author(env: dict, bot: str):
    host = urlsplit(env.get("GITHUB_SERVER_URL") or env.get("GITHUB_API_URL") or "").hostname
    return bot, "%s@users.noreply.%s" % (bot, host or "github.com")


def step(ph, n: int, m: dict, pr: dict, base: str, plan: dict, sess: dict) -> Optional[dict]:
    """The guideline step (7.5): the result dict of `body.summary`, or None. A commit of an
    earlier run (its trailer) is reported instead of pushing again. After a push the phase's
    head is the pushed commit."""
    if m.get("command") not in RULE_OPS:
        return None
    subject = rules_git.receipt(ph.repo, base, ph.head, m["key"])
    if subject is not None:
        _note(n, "이전 실행의 지침 커밋 발견: %s" % subject)
        plan["dropped"] = [d for d in plan["dropped"] if d not in (WRONG_OP, NO_GUIDELINE)]
        return _from_subject(subject)
    if not plan["guideline"]:
        return None
    change = dict(plan["guideline"])
    source = "PR #%d %s (@%s, %s, %s)" % (pr["number"], m.get("url") or "", m["author"],
                                         (m.get("created_at") or "")[:10], m["key"])
    repo_info = (pr.get("head") or {}).get("repo") or {}
    fork = str(repo_info.get("full_name") or "").lower() != ph.env["GITHUB_REPOSITORY"].lower()
    if not fork and change["op"] == "add" and rules.check_path(change["path"]) is None \
            and _overflows(ph.repo, change, source):  # no resume for a path never committed
        change["path"] = _new_path(ph, n, sess, change["path"])
    rid = change["target"] if change["op"] == "remove" else rules.rule_id(change["path"],
                                                                          change["rule"])
    old = _old_rule(ph.repo, change)
    failed = {"status": "failed", "path": change["path"], "rule_id": rid,
              "entry": _entry(rid, change, source)}
    if fork:
        _note(n, "지침 커밋 안 함: " + FORK)
        return dict(failed, reason="%s: %s" % (change["path"], FORK))

    def apply(repo: str) -> dict:
        res = rules_edit.apply_change(repo, change, source)
        if res["status"] in _DONE.values():
            rules_edit.write_indexes(repo)
        return res
    message = "ai-review: %s rule %s %s %s (PR #%d)" % (change["op"], rid, _WORD[change["op"]],
                                                     change["path"], pr["number"])
    result, reason = rules_git.push_change(
        ph.repo, repo_info.get("clone_url") or "", pr["head"]["ref"], apply, message, m["key"],
        _author(ph.env, ph.bot), ph.env["AI_REVIEW_BOT_PAT"])
    if reason:
        return dict(failed, reason="%s: %s" % (change["path"], reason))
    status = (result or {}).get("status")
    head = git(ph.repo, "rev-parse", "HEAD").strip()
    if status in _DONE.values() and head == ph.head:  # a change but no commit: nothing landed
        _note(n, "지침 반영 안 됨: " + NO_COMMIT)
        return dict(failed, reason="%s: %s" % (change["path"], NO_COMMIT))
    if status in _DONE.values():
        ph.head = head
        ph.pushed.add(head)
        _note(n, "지침 커밋 push 완료: %s %s" % (head[:7], message))
        return {"status": status, "path": result["path"], "rule_id": result["rule_id"],
                "old": old, "pushed": True}
    if status == "unchanged":
        return {"status": "unchanged", "path": result["path"], "rule_id": result["rule_id"]}
    why = TOO_LONG if status == "overflow" else (result or {}).get("reason") or status
    _note(n, "지침 반영 안 됨: %s" % why)
    return dict(failed, reason="%s: %s" % (change["path"], why),
                entry=failed["entry"] if status == "overflow" else None)
