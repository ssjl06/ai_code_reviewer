"""Issue ledger: review issues tracked as line regions that follow the code across pushes."""
from __future__ import annotations

import hashlib
from typing import Dict, List, Optional, Set, Tuple

from review.diff_split import parse_unified_diff
from review.previous import _closest
from review.regions import map_region

CLOSED = ("resolved", "waived")
NEAR = 3       # a finding this close to an issue's region is the same issue
SAME_AS = 15   # how far from the region a `same_as` may point
CONTEXT = 20   # recheck context: lines shown on each side of an issue's region


def finding_id(f: dict) -> str:
    """Moved here from `aggregate` so that `unique_id` needs no import of it (aggregate
    imports ledger, not the other way round)."""
    key = "%s:%s:%s" % (f.get("file"), f.get("anchor") or f.get("line"), f.get("title"))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]


def unique_id(f: dict, taken: Set[str]) -> str:
    """`finding_id(f)`, rehashed with a counter suffix while it is taken."""
    base = finding_id(f)
    if base not in taken:
        return base
    n = 1
    while True:
        cand = hashlib.sha1(("%s#%d" % (base, n)).encode("utf-8")).hexdigest()[:8]
        if cand not in taken:
            return cand
        n += 1


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


def _dist(i: dict, line: int) -> int:
    lo, hi = i.get("lo", i["line"]), i.get("hi", i["line"])
    return lo - line if line < lo else max(0, line - hi)


def _closed(i: dict) -> bool:
    return i.get("status", "open") in CLOSED


def classify(findings: List[dict], issues: List[dict]) -> Tuple[List[dict], List[dict], List[dict]]:
    """Split `findings` into `(new, duplicates, links)`. A finding naming an issue in `same_as`
    (an orphan anywhere, any other issue in its file within SAME_AS lines) or lying within NEAR
    lines of a non-orphan issue's region is not new. A closed issue swallows it as a duplicate
    (`dup_of` set); an open one swallows it as a duplicate by position or as a link by `same_as`.
    Position never matches an orphan; an open issue wins over a closed one."""
    by_id = {i["id"]: i for i in issues}
    new, dups, links = [], [], []
    for f in findings:
        target, as_link = None, False
        ref = by_id.get(f.get("same_as"))
        if ref is not None and (ref.get("orphan") or (
                ref["file"] == f["file"] and _dist(ref, f["line"]) <= SAME_AS)):
            target, as_link = ref, not _closed(ref)
        else:
            near = [i for i in issues if not i.get("orphan") and i["file"] == f["file"]
                    and _dist(i, f["line"]) <= NEAR]
            if near:
                target = min(near, key=lambda i: (_closed(i), _dist(i, f["line"])))
        if target is None:
            new.append(f)
        elif as_link:
            links.append({"id": target["id"], "file": f["file"], "line": f["line"]})
        else:
            dups.append(dict(f, dup_of=target["id"]))
    return new, dups, links


def shown_lines(chunks: List[dict], recheck: Dict[int, List[dict]]) -> Dict[int, Dict[str, Set[int]]]:
    """Per chunk index and file, the line numbers the model saw: the right-side lines of the
    chunk diff plus the numbered lines of its recheck items (`start` is the first line of
    `text`)."""
    out: Dict[int, Dict[str, Set[int]]] = {}
    for c in chunks:
        files = out.setdefault(c["index"], {})
        for f in parse_unified_diff(c.get("diff") or ""):
            files.setdefault(f["path"], set()).update(f["right_lines"])
        for item in recheck.get(c["index"], ()):
            n = len(item["text"].split("\n"))
            files.setdefault(item["file"], set()).update(range(item["start"], item["start"] + n))
    return out


def assign_recheck(chunks: List[dict], previous: List[dict], blobs: Dict[str, List[str]],
                   cfg: dict) -> Tuple[Dict[int, List[dict]], List[str]]:
    """Give each issue marked `recheck` (and `pre == "ask"`, not an orphan) the head lines of
    its region with CONTEXT lines on each side, in the first chunk that holds its file.
    Items are `{"id", "file", "lo", "hi", "start", "text"}` (`lo`..`hi` is the shown range,
    `start == lo`, `text` has no trailing newline). Each chunk has its own budget of
    `recheck_max_chars` on top of its diff; an item that does not fit, or whose file is in no
    chunk, is left out: the second value lists those ids. An issue whose file has no blob
    cannot be shown and is neither. Issues marked `pending` (left out by the previous run) are
    served first, the others keep their order, so a left-out issue gets a slot on the next head."""
    budget = int(cfg["recheck_max_chars"])
    used: Dict[int, int] = {}
    by_chunk: Dict[int, List[dict]] = {}
    left: List[str] = []
    for p in sorted(previous, key=lambda p: not p.get("pending")):
        lines = blobs.get(p["file"])
        if (not p.get("recheck") or p.get("pre") != "ask" or p.get("orphan")
                or lines is None):
            continue
        holder = next((c["index"] for c in chunks if p["file"] in c["files"]), None)
        lo = max(1, p.get("lo", p["line"]) - CONTEXT)
        hi = min(len(lines), p.get("hi", p["line"]) + CONTEXT)
        text = "\n".join(lines[lo - 1:hi])
        if holder is None or lo > hi or used.get(holder, 0) + len(text) > budget:
            left.append(p["id"])
            continue
        used[holder] = used.get(holder, 0) + len(text)
        by_chunk.setdefault(holder, []).append(
            {"id": p["id"], "file": p["file"], "lo": lo, "hi": hi, "start": lo, "text": text})
    return by_chunk, left


def pending_after(recheck: Dict[int, List[dict]], left_out: List[str],
                  results: List[dict]) -> List[str]:
    """Ids still to be rechecked: the ones left out, then the ones whose chunk is not `ok`."""
    status = {r["index"]: r.get("status") for r in results}
    out = list(left_out)
    for index in sorted(recheck):
        if status.get(index) != "ok":
            out += [i["id"] for i in recheck[index] if i["id"] not in out]
    return out


def prompt_lists(previous: List[dict], closed: List[dict]) -> Tuple[List[dict], List[dict]]:
    """`(ledger, orphans)` for the reviewer prompt: the open issues of `previous` and the closed
    ones with a region form the ledger; open and closed orphans form the orphan list (open ones
    first). An issue of a file that left the PR (`pre == "resolved"`) is in neither."""
    ledger = [p for p in previous if p.get("pre") not in ("orphan", "resolved")]
    ledger += [c for c in closed if not c.get("orphan")]
    orphans = [p for p in previous if p.get("pre") == "orphan"]
    orphans += [c for c in closed if c.get("orphan")]
    return ledger, orphans


def finish_orphans(prev_out: List[dict], links: List[dict], complete: bool) -> None:
    """Settle open orphans in place. A link moves an orphan to the finding's place and
    reopens it there (it wins over auto-resolution); a link to any other entry changes
    nothing. An orphan nothing linked is resolved when the run was complete."""
    by_id: Dict[str, dict] = {}
    for link in links:
        by_id.setdefault(link["id"], link)
    for e in prev_out:
        if e.get("pre") != "orphan" or e.get("status") != "open":
            continue
        link = by_id.get(e["id"])
        if link is not None:
            e["file"], e["line"], e["lo"], e["hi"] = (link["file"], link["line"],
                                                      link["line"], link["line"])
            e["orphan"] = False
        elif complete:
            e["status"] = "resolved"
