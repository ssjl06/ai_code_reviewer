"""Seen-set update after a review: which line fingerprints now count as reviewed."""
from __future__ import annotations

from collections import Counter
from typing import Dict, List

from review.diff_split import parse_unified_diff
from review.hunks import diff_prints

Seen = Dict[str, Counter]


def _add(dst: Seen, src: Seen) -> None:
    for path, c in src.items():
        dst.setdefault(path, Counter()).update(c)


def _prints(diff: str, blobs: Dict[str, List[str]]) -> Seen:
    """Prints per path; a chunk may hold several units (header + hunks) of one file."""
    out: Seen = {}
    for f in parse_unified_diff(diff):
        _add(out, diff_prints(f["text"], blobs))
    return out


def update_seen(scope: dict, chunks: List[dict], results: List[dict]) -> Seen:
    """A print counts as reviewed only when every copy of it in the targets went through
    an `ok` chunk; otherwise the previous count is kept (capped at the head count)."""
    blobs = scope["blobs"]
    status = {r["index"]: r.get("status") for r in results}
    ok: Seen = {}
    bad: Seen = {}
    for c in chunks:
        _add(ok if status.get(c["index"]) == "ok" else bad, _prints(c["diff"], blobs))
    for t in scope["targets"]:
        left = _prints(t["text"], blobs).get(t["path"], Counter())
        _add(bad, {t["path"]: left - ok.get(t["path"], Counter())})
    out: Seen = {}
    for path, head in scope["head_prints"].items():
        o, b = ok.get(path, Counter()), bad.get(path, Counter())
        prev = scope["prev_seen"].get(path, Counter())
        c = Counter({p: n if (o[p] > 0 and b[p] <= 0) else min(prev[p], n)
                     for p, n in head.items()})
        c = +c
        if c:
            out[path] = c
    return out
