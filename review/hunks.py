"""Select the diff hunks that contain not-yet-reviewed lines (content fingerprints)."""
from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional, Set

from review.diff_split import HUNK_RE, parse_unified_diff, split_hunks
from review.fingerprint import anchor_line, fp_line, fp_removed, whole_hash


def scan_hunk(hunk: str, lines: List[str]) -> List[dict]:
    """Changes of one hunk as {"kind", "line", "fp", "text"}; `lines` is the head file."""
    rows = hunk.split("\n")
    m = HUNK_RE.match(rows[0])
    if not m:
        return []
    n, out = int(m.group(1)), []
    for row in rows[1:]:
        if row.startswith("+"):
            out.append({"kind": "+", "line": n, "fp": fp_line(lines, n), "text": row[1:]})
            n += 1
        elif row.startswith(" "):
            n += 1
        elif row.startswith("-"):
            out.append({"kind": "-", "line": anchor_line(lines, n),
                        "fp": fp_removed(lines, n, row[1:]), "text": row[1:]})
    return out


def select_file(f: dict, lines: List[str], seen: Optional[Counter],
                whole: Optional[bytes]) -> dict:
    header, hunks = split_hunks(f["text"])
    scans = [scan_hunk(h, lines) for h in hunks]
    head = Counter(c["fp"] for ch in scans for c in ch)
    eff = head if whole is not None and whole == whole_hash(head) else (seen or Counter())
    kept: List[str] = []
    added: Set[int] = set()
    deleted: Set[int] = set()
    for h, ch in zip(hunks, scans):
        new = [c for c in ch if head[c["fp"]] > eff[c["fp"]]]
        if new:
            kept.append(h)
        added.update(c["line"] for c in new if c["kind"] == "+")
        deleted.update(c["line"] for c in new if c["kind"] == "-")
    parsed = parse_unified_diff(header + "".join(kept)) if kept else []
    return {"head": head, "file": parsed[0] if parsed else None,
            "added": added, "deleted": deleted}


def diff_prints(diff: str, blobs: Dict[str, List[str]]) -> Dict[str, Counter]:
    out: Dict[str, Counter] = {}
    for f in parse_unified_diff(diff):
        if f["path"] not in blobs:
            continue
        _, hunks = split_hunks(f["text"])
        out.setdefault(f["path"], Counter()).update(
            c["fp"] for h in hunks for c in scan_hunk(h, blobs[f["path"]]))
    return out
