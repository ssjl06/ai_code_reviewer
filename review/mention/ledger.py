"""A mention's changes on a review state: waive, add, upgrade; the marker; the verdict."""
from __future__ import annotations

from typing import Tuple

from review.ledger import CLOSED
from review.scope import _record
from review.state_fit import fit_state


def _open(i: dict) -> bool:
    return i.get("status", "open") not in CLOSED


def apply_changes(state: dict, changes: dict) -> Tuple[dict, dict]:
    """A copy of the normalized `state` with the mention's `changes` applied, and what took
    effect. `w` waives an open issue, `n` adds a record at the front as open unless its id
    exists (any status), `up` makes an open non-blocking issue blocking. Nothing else changes."""
    waive, upgrade = set(changes.get("w") or []), set(changes.get("up") or [])
    done = {"w": [], "n": [], "up": []}
    issues = []
    for i in state.get("issues") or []:
        if _open(i):
            if i["id"] in waive and i["id"] not in done["w"]:
                i = dict(i, status="waived")
                done["w"].append(i["id"])
            elif i["id"] in upgrade and not i.get("blocking") and i["id"] not in done["up"]:
                i = dict(i, blocking=True)
                done["up"].append(i["id"])
        issues.append(i)
    known = {i["id"] for i in issues}
    added = []
    for rec in changes.get("n") or []:
        if rec["id"] not in known:
            known.add(rec["id"])
            added.append(dict(rec, status="open"))
            done["n"].append(rec["id"])
    new = dict(state, issues=added + issues)
    new.pop("waived_now", None)
    return new, done


def marker_state(state: dict, m: dict) -> dict:
    """The dict for the state marker: `state` with the receipt `m`, fitted to the budget."""
    st = {"v": 3, "sha": state["sha"], "complete": state["complete"],
          "seen": dict(state["seen"]), "seen_files": dict(state["seen_files"]),
          "seen_hunks": dict(state["seen_hunks"]),
          "issues": [_record(i) for i in state["issues"]], "closed": {},
          "recheck_pending": list(state["recheck_pending"]),
          "approvable": bool(state["approvable"])}
    if state.get("dropped_closed"):
        st["dropped_closed"] = state["dropped_closed"]
    if state.get("cut"):
        st["cut"] = dict(state["cut"])
    if state.get("unclear") is not None:
        st["unclear"] = list(state["unclear"])
    st["m"] = m
    return fit_state(st, partial=set(state["seen"]))


def ledger_event(state: dict, current: bool, added_blocking: bool) -> str:
    """The review verdict after a mention (spec 6.2)."""
    unclear = state.get("unclear")
    if unclear is None:  # an old marker: never an approval
        return "REQUEST_CHANGES" if added_blocking else "COMMENT"
    opened = [i for i in state["issues"] if _open(i)]
    if any(i.get("blocking") and i["id"] not in unclear for i in opened):
        return "REQUEST_CHANGES"
    if (state.get("approvable") and current
            and not any(i["id"] in unclear for i in opened)):
        return "APPROVE"
    return "COMMENT"
