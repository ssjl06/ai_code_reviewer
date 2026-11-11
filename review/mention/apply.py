"""Turn the model's answer to one mention into checked actions (spec 5.4).

Nothing the model outputs is applied without these checks. `plan_actions` is pure: the caller
supplies what it read from git and the PR (`covered`, `hunk_lines`, `same_line`, `anchor`).
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Set

from review.ledger import CLOSED, classify, finding_id, new_issue, unique_id

RULE_OPS = {"rule-add": "add", "rule-update": "update", "rule-remove": "remove"}
NO_STATE = "아직 리뷰가 없어 이슈로 등록하지 않음"
NOT_COVERED = "리뷰 대상이 아닌 파일이라 이슈로 등록하지 않음"
OUT_OF_DIFF = "diff 밖이라 이슈로 등록하지 않음"
NOT_AT_HEAD = "이 파일은 아직 리뷰되지 않은 변경이 있어 이슈로 등록하지 않음"
WRONG_OP = "명령과 다른 지침 작업"


def _withdrawals(out: dict, m: dict, targets: List[dict], plan: dict) -> None:
    """Only a `/철회` command and a verdict with `withdraw: true` on a target waive it."""
    if m.get("command") != "withdraw":
        return
    verdicts: Dict[str, dict] = {}
    for v in out.get("verdicts") or []:
        verdicts.setdefault(v["id"], v)  # the first verdict of an id counts
    for t in targets:
        v = verdicts.get(t["id"])
        if not v or not v.get("withdraw") or t["id"] in plan["changes"]["w"] + plan["done"]:
            continue
        status = t.get("status", "open")
        if status == "waived":
            plan["done"].append(t["id"])
        elif status in CLOSED:
            plan["dropped"].append("이슈 %s는 이미 해결되어 철회할 수 없음" % t["id"])
        else:
            plan["changes"]["w"].append(t["id"])


def _skip_note(f: dict, state: Optional[dict], hunk_lines, covered, same_line) -> Optional[str]:
    if state is None:
        return NO_STATE
    if f["file"] not in covered:
        return NOT_COVERED
    if f["line"] not in hunk_lines.get(f["file"], ()):
        return OUT_OF_DIFF
    if not same_line(f["file"]):
        return NOT_AT_HEAD
    return None


def _new_issues(out: dict, state: Optional[dict], hunk_lines, covered, same_line, anchor,
                cfg: dict, plan: dict) -> None:
    issues = list(state["issues"]) if state else []
    by_id = {i["id"]: i for i in issues}
    taken = set(by_id)
    seen: Set[str] = set()
    changes, notes = plan["changes"], plan["notes"]

    def note(text: str) -> None:
        if text not in notes:
            notes.append(text)
    for raw in out.get("new_issues") or []:
        if raw["confidence"] < cfg["min_confidence"]:
            continue
        f = dict(raw)
        if anchor is not None:
            f["anchor"] = anchor(f["file"], f["line"])
        if finding_id(f) in seen:
            continue
        seen.add(finding_id(f))
        why = _skip_note(f, state, hunk_lines, covered, same_line)
        if why:
            note(why)
            continue
        in_block = f.get("severity") in cfg["block_severities"]
        f["blocking"] = in_block and f["confidence"] >= cfg["block_confidence"]
        f["needs_check"] = in_block and not f["blocking"]
        new, dups, links = classify([f], issues)
        for d in dups:
            note("이미 이슈 %s로 등록됨" % d["dup_of"])
        for link in links:
            target = by_id.get(link["id"])
            if (link["blocking"] and target is not None and not target.get("blocking")
                    and link["id"] not in changes["up"]):
                changes["up"].append(link["id"])
        for g in new:
            g["id"] = unique_id(g, taken)
            taken.add(g["id"])
            rec = new_issue(g)
            issues.append(rec)
            by_id[g["id"]] = rec
            changes["n"].append(rec)
            plan["findings"].append(g)


def _guideline(out: dict, m: dict, plan: dict) -> None:
    g = out.get("guideline")
    op = RULE_OPS.get(m.get("command"))
    if not g or op is None:
        return
    if g.get("op") != op:
        plan["dropped"].append(WRONG_OP)
        return
    plan["guideline"] = g


def plan_actions(out: dict, m: dict, targets: List[dict], state: Optional[dict],
                 hunk_lines: Dict[str, Set[int]], covered: Set[str],
                 same_line: Callable[[str], bool], anchor, cfg: dict) -> dict:
    """What of `out` may be applied for mention `m`: `changes` (`w` ids to waive, `n` new
    ledger records, `up` ids to make blocking), `findings` (the full findings of `n`, with
    `id` and `blocking`), `guideline` (the model's change or None), `done` (targets already
    waived), `dropped` (reasons shown as "반영 안 됨"), `notes` (issues not registered) and
    `hint` (`out["wants"]`, only for a mention without a command)."""
    plan = {"changes": {"w": [], "n": [], "up": []}, "findings": [], "guideline": None,
            "done": [], "dropped": [], "notes": [], "hint": None}
    _withdrawals(out, m, targets, plan)
    _new_issues(out, state, hunk_lines, covered, same_line, anchor, cfg, plan)
    _guideline(out, m, plan)
    if m.get("command") is None:
        plan["hint"] = out.get("wants")
    return plan
