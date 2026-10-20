"""Find previous review findings again by line content (pure functions)."""
from __future__ import annotations

from typing import Callable, Dict, List, Sequence, Set

from review.fingerprint import fp_line

GONE_NOTE = "파일이 PR에서 빠짐"
DELETED_NOTE = "파일이 삭제됨"


def _closest(lines: List[str], anchor: str, old: int):
    if not anchor:
        return None
    hits = [n for n in range(1, len(lines) + 1) if fp_line(lines, n).hex() == anchor]
    return min(hits, key=lambda n: (abs(n - old), n)) if hits else None


def relocate(prev: List[dict], live: Set[str], blobs: Dict[str, List[str]],
             targets: Set[str], renames: Dict[str, str], deleted: Set[str] = frozenset(),
             search: Sequence[str] = ()) -> List[dict]:
    """`renames` maps a state path to its head path; `live` are the PR paths present at head.
    A finding whose file left the PR is resolved, unless the file is gone from head (deleted
    or renamed along an unknown chain) and its anchor is found in one of `search`."""
    out = []
    for p in prev:
        e = dict(p)
        e["file"] = renames.get(e["file"], e["file"])
        anchor, old = e.get("anchor") or "", e.get("line", 0)
        if e["file"] not in live and e["file"] not in blobs:
            for path in search:
                found = _closest(blobs.get(path, []), anchor, old)
                if found is not None:
                    e["file"] = path
                    break
        if e["file"] not in live:
            e.update(pre="resolved", located=False,
                     note=DELETED_NOTE if e["file"] in deleted else GONE_NOTE)
            out.append(e)
            continue
        found = _closest(blobs.get(e["file"], []), anchor, old)
        e["located"] = found is not None
        if found is not None:
            e["line"] = found
        e["pre"] = "ask" if e["file"] in targets else "open"
        out.append(e)
    return out


def anchor_of(blobs: Dict[str, List[str]]) -> Callable[[str, int], str]:
    def fn(path: str, line: int) -> str:
        return fp_line(blobs[path], line).hex() if path in blobs else ""
    return fn
