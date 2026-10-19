"""Select the diff hunks that contain not-yet-reviewed lines (content fingerprints)."""
from __future__ import annotations

import re
from collections import Counter
from typing import Dict, List, Optional, Set

from review.diff_split import HUNK_RE, parse_unified_diff, split_hunks
from review.fingerprint import anchor_line, fp_line, fp_removed, whole_hash

_FULL = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


def split_hunk(hunk: str, limit: int) -> List[str]:
    """Consecutive valid sub-hunks of about `limit` characters with recomputed headers.
    A `\\ No newline` row stays with the row before it; one oversized row is its own
    sub-hunk. The new start is the line counter `scan_hunk` would have at that point, so
    the fingerprints of the sub-hunks equal those of the whole hunk."""
    rows = hunk.split("\n")
    m = _FULL.match(rows[0])
    if len(hunk) <= limit or not m:
        return [hunk]
    atoms: List[List[str]] = []
    for r in rows[1:-1] if rows[-1] == "" else rows[1:]:
        if r.startswith("\\") and atoms:
            atoms[-1].append(r)
        else:
            atoms.append([r])
    room, slices, size = limit - len(rows[0]) - 8, [[]], 0
    for a in atoms:
        n = sum(len(r) + 1 for r in a)
        if slices[-1] and size + n > room:
            slices.append([])
            size = 0
        slices[-1] += a
        size += n
    old_pos = int(m.group(1)) + (0 if m.group(2) != "0" else 1)
    new_pos, out = int(m.group(3)), []
    for rs in slices:
        old = sum(1 for r in rs if r[:1] in (" ", "-"))
        new = sum(1 for r in rs if r[:1] in (" ", "+"))
        out.append("@@ -%d,%d +%d,%d @@%s\n%s\n" % (old_pos if old else old_pos - 1, old,
                                                    new_pos, new, m.group(5), "\n".join(rs)))
        old_pos, new_pos = old_pos + old, new_pos + new
    return out


def _hunks(f: dict, limit_chars: Optional[int]):
    """(header, hunks); with `limit_chars` (the chunk size in characters) a hunk larger
    than `limit_chars - len(header) - 200` is split into sub-hunks."""
    header, hunks = split_hunks(f["text"])
    if limit_chars is None:
        return header, hunks
    room = limit_chars - len(header) - 200
    return header, [s for h in hunks for s in split_hunk(h, room)]


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
                whole: Optional[bytes], limit_chars: Optional[int] = None) -> dict:
    header, hunks = _hunks(f, limit_chars)
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


def near_hunks(f: dict, near: List[int], limit_chars: Optional[int]) -> Optional[dict]:
    """The file reduced to the (sub-)hunks closest to each line in `near`; None if no hunk."""
    header, hunks = _hunks(f, limit_chars)
    rights = [parse_unified_diff(header + h)[0]["right_lines"] for h in hunks]
    pick: Set[int] = set()
    for line in near if hunks else []:
        pick.add(min(range(len(hunks)), key=lambda i: (
            min((abs(n - line) for n in rights[i]), default=1 << 30), i)))
    if not pick:
        return None
    return parse_unified_diff(header + "".join(hunks[i] for i in sorted(pick)))[0]


def diff_prints(diff: str, blobs: Dict[str, List[str]]) -> Dict[str, Counter]:
    out: Dict[str, Counter] = {}
    for f in parse_unified_diff(diff):
        if f["path"] not in blobs:
            continue
        _, hunks = split_hunks(f["text"])
        out.setdefault(f["path"], Counter()).update(
            c["fp"] for h in hunks for c in scan_hunk(h, blobs[f["path"]]))
    return out
