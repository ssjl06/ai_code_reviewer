"""Issue ledger: review issues tracked as line regions that follow the code across pushes."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from review.previous import _closest
from review.regions import map_region

CLOSED = ("resolved", "waived")


def new_issue(f: dict) -> dict:
    line = f["line"]
    return {"id": f["id"], "file": f["file"], "line": line, "lo": line, "hi": line,
            "status": "open", "severity": f["severity"], "blocking": f["blocking"],
            "anchor": f.get("anchor", ""), "title": f["title"],
            "excerpt": (f.get("detail") or "")[:200], "orphan": False}


def from_v2(entry: dict) -> dict:
    line = entry["line"]
    return {"id": entry["id"], "file": entry["file"], "line": line, "lo": line, "hi": line,
            "status": "open", "severity": entry["severity"], "blocking": entry["blocking"],
            "anchor": entry.get("anchor", ""), "title": entry["title"],
            "excerpt": "", "orphan": False}


def _by_anchor(i: dict, blobs: Dict[str, List[str]]) -> None:
    found = _closest(blobs.get(i["file"], []), i.get("anchor") or "", i["line"])
    if found is None:
        return
    d = found - i["line"]
    i["line"], i["lo"], i["hi"] = found, i["lo"] + d, i["hi"] + d


def advance(issues: List[dict], u0: Optional[Dict[str, dict]],
            blobs: Dict[str, List[str]]) -> Tuple[List[dict], bool]:
    """Move every issue to the new head. `u0` is `regions.parse_u0` of the diff between the
    previously reviewed head and this one, or None when that head is unknown: the issues are
    then relocated by anchor (the second value is True) or keep their region."""
    out = []
    for src in issues:
        i = dict(src)
        out.append(i)
        if i["orphan"]:
            continue
        if u0 is None:
            _by_anchor(i, blobs)
            continue
        entry = u0.get(i["file"])
        if entry is None:
            continue
        if entry["deleted"]:
            i["orphan"] = True
            continue
        mapped = map_region(entry["hunks"], i["line"], i["lo"], i["hi"])
        i["file"] = entry["path"]
        if mapped is None:
            i["orphan"] = True
        else:
            i["line"], i["lo"], i["hi"] = mapped
    return out, u0 is None
