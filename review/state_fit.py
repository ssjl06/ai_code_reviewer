"""Keep the state marker within its character budget (GitHub caps a review body at 65,536)."""
from __future__ import annotations

import base64
from typing import AbstractSet, Any

from review.fingerprint import unpack, whole_hash
from review.render import state_marker

MARKER_BUDGET = 40000


def _len(obj: Any) -> int:
    """Characters `obj` takes inside the serialized marker."""
    return len(state_marker(obj)) - len(state_marker(0)) + 1


def fit_state(state: dict, budget: int = MARKER_BUDGET,
              partial: AbstractSet[str] = frozenset()) -> dict:
    """In order, until the marker fits: move the largest files from seen to seen_files
    (`partial` files, not fully reviewed, last: their hash never matches), drop seen,
    seen_files and seen_hunks (the next run reviews everything again), drop the oldest
    non-blocking findings (state order is newest first). Blocking findings always stay."""
    st = dict(state, seen=dict(state.get("seen") or {}),
              seen_files=dict(state.get("seen_files") or {}),
              findings=list(state.get("findings") or []))
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
    if total > budget:
        st["seen"], st["seen_files"] = {}, {}
        if "seen_hunks" in st:
            st["seen_hunks"] = {}
        total = len(state_marker(st))
    found = st["findings"]
    for i in range(len(found) - 1, -1, -1):
        if total <= budget:
            break
        if not found[i].get("blocking"):
            total -= _len(found[i]) + 1
            del found[i]
            if total <= budget:
                total = len(state_marker(st))
    return st
