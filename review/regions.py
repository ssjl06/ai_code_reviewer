"""Map issue line regions from one pushed head to the next using `git diff -U0` hunks."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from review.diff_split import HUNK_RE, parse_unified_diff

Hunk = Tuple[int, int, int, int]  # (old_start, old_len, new_start, new_len)
RADIUS = 5

# HUNK_RE only captures the new start; the mapping needs all four numbers.
_FULL_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _hunks_of(text: str) -> List[Hunk]:
    out = []
    for ln in text.split("\n"):
        if not HUNK_RE.match(ln):
            continue
        m = _FULL_RE.match(ln)
        out.append((int(m.group(1)), 1 if m.group(2) is None else int(m.group(2)),
                    int(m.group(3)), 1 if m.group(4) is None else int(m.group(4))))
    return out


def parse_u0(diff: str) -> Dict[str, dict]:
    """Old path -> {"path": new path, "deleted": bool, "hunks": [Hunk]} from a -U0 diff."""
    out = {}
    for f in parse_unified_diff(diff):
        old = f["old_path"] or f["path"]
        out[old] = {"path": f["path"], "deleted": f["status"] == "D",
                    "hunks": _hunks_of(f["text"])}
    return out


def map_line(hunks: List[Hunk], line: int) -> int:
    shift = 0
    for os_, ol, ns, nl in hunks:
        if ol == 0:  # pure insertion after old line `os_`
            if line > os_:
                shift += nl
        elif line >= os_ + ol:
            shift += nl - ol
        elif line >= os_:
            if nl == 0:
                return max(1, ns)
            return ns + min(nl - 1, (line - os_) * nl // ol)
    return line + shift


def _deleted(hunks: List[Hunk], line: int) -> bool:
    return any(ol > 0 and nl == 0 and os_ <= line < os_ + ol for os_, ol, _, nl in hunks)


def _rewrite(hunks: List[Hunk], line: int) -> Optional[Hunk]:
    """The hunk that replaces `line` with other lines, if any."""
    return next((h for h in hunks if h[3] > 0 and h[0] <= line < h[0] + h[1]), None)


def map_region(hunks: List[Hunk], line: int, lo: int, hi: int) -> Optional[Tuple[int, int, int]]:
    """Map (line, lo, hi); None when every line of [lo, hi] was deleted. An end inside a
    rewritten hunk takes that hunk's new start or end, and the region is then cut to RADIUS
    lines around the mapped line. A pure insertion (no old lines) never widens a region."""
    if hi >= lo and all(_deleted(hunks, n) for n in range(lo, hi + 1)):
        return None
    new_line = map_line(hunks, line)
    first, last = _rewrite(hunks, lo), _rewrite(hunks, hi)
    new_lo = first[2] if first else map_line(hunks, lo)
    new_hi = last[2] + last[3] - 1 if last else map_line(hunks, hi)
    return new_line, max(new_lo, new_line - RADIUS), min(new_hi, new_line + RADIUS)
