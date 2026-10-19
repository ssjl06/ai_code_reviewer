"""Find previous review findings again by line content (pure functions)."""
from __future__ import annotations

from typing import Callable, Dict, List, Set

from review.fingerprint import fp_line

GONE_NOTE = "파일이 PR에서 빠짐"


def _closest(lines: List[str], anchor: str, old: int):
    if not anchor:
        return None
    hits = [n for n in range(1, len(lines) + 1) if fp_line(lines, n).hex() == anchor]
    return min(hits, key=lambda n: (abs(n - old), n)) if hits else None


def relocate(prev: List[dict], pr_paths: Set[str], blobs: Dict[str, List[str]],
             targets: Set[str], renames: Dict[str, str]) -> List[dict]:
    out = []
    for p in prev:
        e = dict(p)
        e["file"] = renames.get(e["file"], e["file"])
        if e["file"] not in pr_paths:
            e.update(pre="resolved", note=GONE_NOTE, located=False)
            out.append(e)
            continue
        found = _closest(blobs.get(e["file"], []), e.get("anchor") or "", e.get("line", 0))
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
