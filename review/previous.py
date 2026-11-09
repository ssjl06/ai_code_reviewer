"""Describe the previous review's open issues for this run (pure functions)."""
from __future__ import annotations

from typing import Callable, Dict, List, Set

from review.fingerprint import fp_line

GONE_NOTE = "파일이 PR에서 빠짐"
DELETED_NOTE = "파일이 삭제됨"
REVERTED_NOTE = "PR 변경이 되돌려짐"


def _closest(lines: List[str], anchor: str, old: int):
    if not anchor:
        return None
    hits = [n for n in range(1, len(lines) + 1) if fp_line(lines, n).hex() == anchor]
    return min(hits, key=lambda n: (abs(n - old), n)) if hits else None


def annotate(issues: List[dict], live: Set[str], targets: Set[str],
             gone: Set[str] = frozenset(), lost: Set[str] = frozenset(),
             weak: bool = False, reverted: Set[str] = frozenset()) -> List[dict]:
    """Describe open ledger issues (already moved to head) for the prompt and the verdict.
    `pre` is "resolved" when the issue's file left the PR (with a note; a deleted file says so)
    or is in `reverted`, PR files without any hunk that the PR no longer changes (also with
    `weak`), "orphan" for an issue without a region, "ask" when its file is a target of this
    run and "open" otherwise. `lost` holds the ids that could not be located (weak tracking): they
    get `located=False`, so `scope._recheck` asks about them or the verdict calls them
    unclear. With `weak` (no previous head to diff against) an issue whose file is not in the PR
    cannot be told from a renamed one, so it is not resolved: `pre="ask"`, `located=False`
    (unclear unless the model says otherwise). Code that moved elsewhere is not looked for."""
    out = []
    for i in issues:
        e = dict(i)
        if e["file"] in reverted:
            e.update(pre="resolved", located=False, orphan=False, note=REVERTED_NOTE)
        elif e["file"] not in live and weak:
            e.update(pre="ask", located=False, orphan=False)
        elif e["file"] not in live:
            # not an orphan to the matcher: a `same_as` link to an issue that is about to be
            # resolved would swallow a finding and hide the defect
            e.update(pre="resolved", located=False, orphan=False,
                     note=DELETED_NOTE if e["file"] in gone else GONE_NOTE)
        else:
            e["located"] = e["id"] not in lost
            e["pre"] = "orphan" if e.get("orphan") else (
                "ask" if e["file"] in targets else "open")
        out.append(e)
    return out


def anchor_of(blobs: Dict[str, List[str]]) -> Callable[[str, int], str]:
    def fn(path: str, line: int) -> str:
        return fp_line(blobs[path], line).hex() if path in blobs else ""
    return fn
