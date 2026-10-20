"""Seen-set update after a review: which line fingerprints now count as reviewed."""
from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional, Set

from review.hunks import diff_hunk_keys, diff_prints

Seen = Dict[str, Counter]


def _add(dst: Seen, src: Seen) -> None:
    for path, c in src.items():
        dst.setdefault(path, Counter()).update(c)


def update_seen(scope: dict, chunks: List[dict], results: List[dict]) -> Seen:
    """A print counts as reviewed only when every copy of it in the targets went through
    an `ok` chunk (or a hunk skipped as reviewed by its key: `prior`); otherwise the
    previous count is kept (capped at the head count)."""
    blobs = scope["blobs"]
    status = {r["index"]: r.get("status") for r in results}
    ok: Seen = {p: Counter(c) for p, c in scope.get("prior", {}).items()}
    bad: Seen = {}
    for c in chunks:
        _add(ok if status.get(c["index"]) == "ok" else bad, diff_prints(c["diff"], blobs))
    for t in scope["targets"]:
        left = diff_prints(t["text"], blobs).get(t["path"], Counter())
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


def update_hunks(scope: dict, chunks: List[dict], results: List[dict],
                 seen: Optional[Seen] = None) -> Dict[str, Set[bytes]]:
    """Hunk keys reviewed so far, kept only for files not yet fully reviewed line by line:
    (previous keys | keys of ok chunks) & keys of the head file."""
    seen = update_seen(scope, chunks, results) if seen is None else seen
    status = {r["index"]: r.get("status") for r in results}
    ok: Dict[str, Set[bytes]] = {}
    for c in chunks:
        if status.get(c["index"]) == "ok":
            for path, keys in diff_hunk_keys(c["diff"], scope["blobs"]).items():
                ok.setdefault(path, set()).update(keys)
    out: Dict[str, Set[bytes]] = {}
    for path, head in scope["head_prints"].items():
        if seen.get(path, Counter()) == head:
            continue
        keys = (set(scope.get("prev_hunks", {}).get(path, ())) | ok.get(path, set())) \
            & scope.get("hunk_keys", {}).get(path, set())
        if keys:
            out[path] = keys
    return out
