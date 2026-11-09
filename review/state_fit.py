"""Keep the state marker within its character budget (GitHub caps a review body at 65,536)."""
from __future__ import annotations

import base64
from typing import AbstractSet, Any, Callable, Dict, List, Tuple

from review.fingerprint import unpack, whole_hash
from review.ledger import CLOSED
from review.render import state_marker
from review.validate import compact_issue, expand_issue

MARKER_BUDGET = 40000
_DISPLAY = ("title", "excerpt")


def _len(obj: Any) -> int:
    """Characters `obj` takes inside the serialized marker."""
    return len(state_marker(obj)) - len(state_marker(0)) + 1


def _fits(st: dict, budget: int) -> bool:
    return len(state_marker(st)) <= budget


def _fewest(n: int, ok: Callable[[int], bool]) -> int:
    """The smallest k in 0..n with `ok(k)`, or n; `ok` is monotone (a larger k only helps)."""
    lo, hi = 0, n
    while lo < hi:
        mid = (lo + hi) // 2
        if ok(mid):
            hi = mid
        else:
            lo = mid + 1
    return lo


def _move_seen(st: dict, budget: int, partial: AbstractSet[str]) -> None:
    """Move the largest files from seen to seen_files (`partial` files, not fully reviewed,
    last: their hash never matches)."""
    seen, files = st["seen"], st["seen_files"]
    total = len(state_marker(st))
    order = sorted(seen, key=lambda p: (p not in partial, _len({p: seen[p]}), p))
    while order and total > budget:
        path = order.pop()
        total -= _len({path: seen[path]}) - 1
        files[path] = base64.b64encode(whole_hash(unpack(seen.pop(path)))).decode("ascii")
        total += _len({path: files[path]}) - 1
        if total <= budget:
            total = len(state_marker(st))  # the estimate may be off by a comma


def _drop_seen(st: dict) -> None:
    """The next run reviews the whole PR again: the failure the ledger is meant to prevent."""
    st["seen"], st["seen_files"] = {}, {}
    if "seen_hunks" in st:
        st["seen_hunks"] = {}


def _compact_closed(st: dict) -> List[Tuple[str, str]]:
    """Move the closed issues into the compact `closed` map. Returns its records, newest first
    (the issues in state order, then the records the map already held)."""
    recs = [(i["file"], compact_issue(i)) for i in st["issues"] if i.get("status") in CLOSED]
    for path, text in (st.get("closed") or {}).items():
        recs += [(path, t) for t in text.split()]
    st["issues"] = [i for i in st["issues"] if i.get("status") not in CLOSED]
    return recs


def _closed_map(recs: List[Tuple[str, str]]) -> Dict[str, str]:
    out: Dict[str, List[str]] = {}
    for path, tok in recs:
        out.setdefault(path, []).append(tok)
    return {p: " ".join(t) for p, t in out.items()}


def _blank_order(issues: List[dict]) -> List[int]:
    """Indexes of the open issues in blanking order, oldest first (state order is newest
    first): the non-blocking ones, then the blocking ones."""
    oldest = list(reversed(range(len(issues))))
    return sorted(oldest, key=lambda n: bool(issues[n].get("blocking")))


def _blanked(issues: List[dict], order: List[int], k: int) -> List[dict]:
    out = list(issues)
    for n in order[:k]:
        out[n] = {key: v for key, v in issues[n].items() if key not in _DISPLAY}
    return out


def fit_state(state: dict, budget: int = MARKER_BUDGET,
              partial: AbstractSet[str] = frozenset()) -> dict:
    """In order, until the marker fits: move the largest files from seen to seen_files, move
    the closed issues into the compact `closed` map (title, excerpt and anchor go), drop title
    and excerpt of the open non-blocking issues, then of the open blocking ones (oldest first),
    drop seen, seen_files and seen_hunks (the display text that then fits is kept), then drop
    the oldest compact closed records and count them in `dropped_closed`. Open issues,
    `recheck_pending` and `cut` (the prints of lines too long for a chunk: small, and their
    loss would allow an approval) are never removed."""
    st = dict(state, seen=dict(state.get("seen") or {}),
              seen_files=dict(state.get("seen_files") or {}),
              issues=[dict(i) for i in state.get("issues") or []])
    _move_seen(st, budget, partial)
    if _fits(st, budget):
        return st
    recs = _compact_closed(st)
    st["closed"] = _closed_map(recs)
    if _fits(st, budget):
        return st
    base = list(st["issues"])
    order = _blank_order(base)

    def blank_fewest() -> None:
        k = _fewest(len(order), lambda k: _fits(dict(st, issues=_blanked(base, order, k)), budget))
        st["issues"] = _blanked(base, order, k)
    blank_fewest()
    if _fits(st, budget):
        return st
    _drop_seen(st)
    blank_fewest()
    if _fits(st, budget):
        return st

    before = int(st.get("dropped_closed") or 0)

    def kept(n: int) -> dict:
        return dict(st, closed=_closed_map(recs[:n]), dropped_closed=before + len(recs) - n)
    d = _fewest(len(recs), lambda d: _fits(kept(len(recs) - d), budget))
    return kept(len(recs) - d)
