"""Issue ledger: review issues tracked as line regions that follow the code across pushes."""
from __future__ import annotations

import hashlib
from typing import Dict, Iterable, List, Optional, Set, Tuple

from review.diff_split import parse_unified_diff
from review.fingerprint import fp_line, norm
from review.previous import _closest
from review.regions import map_region

CLOSED = ("resolved", "waived")
NEAR = 3       # a finding this close to an issue's region is the same issue
SAME_AS = 15   # how far from the region a `same_as` may point
CONTEXT = 20   # recheck context: lines shown on each side of an issue's region
LINE_MAX = 500  # recheck text: longer context lines are cut, so one item stays bounded
REGION_LINE_MAX = 4000  # recheck text: longer lines of the issue's region are cut
LINE_CUT = " …(줄 잘림)"
SEV_RANK = {"critical": 0, "major": 1, "minor": 2, "info": 3}  # lower is more severe


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
            "excerpt": (f.get("detail") or "")[:200], "orphan": False,
            **({"via": f["via"]} if f.get("via") else {})}


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


def moved_lookups(issues: List[dict], u0: Dict[str, dict]) -> List[Tuple[str, str]]:
    """`(old path, head path)` of each open issue whose whole region was deleted from a file
    that still exists: `advance` searches those files for the lines."""
    out = []
    for i in issues:
        entry = None if i["orphan"] or _closed(i) else u0.get(i["file"])
        if entry and not entry["deleted"] and map_region(
                entry["hunks"], i["line"], i["lo"], i["hi"]) is None:
            out.append((i["file"], entry["path"]))
    return out


def _relocate(i: dict, old: Optional[List[str]], head: Optional[List[str]]) -> bool:
    """Move `i` to the copy of its old lines nearest its old place: contiguous lines of `head`
    equal to them under `fingerprint.norm` (the seen-set's normalization), else the 3-line
    window of the primary line (`fp_line`, as anchors), the hit nearest the old line. The
    region moves by the same offset, clipped to `head`. False when neither is found, or
    either blob is missing."""
    lo, n = i["lo"], i["hi"] - i["lo"] + 1
    want = [norm(t) for t in (old or [])[lo - 1:i["hi"]]]
    if len(want) != n or head is None:
        return False
    flat = [norm(t) for t in head]
    hits = [s for s in range(1, len(flat) - n + 2) if flat[s - 1:s - 1 + n] == want]
    if hits:
        d = min(hits, key=lambda s: (abs(s - lo), s)) - lo
    else:
        found = _closest(head, fp_line(old, i["line"]).hex(), i["line"])
        if found is None:
            return False
        d = found - i["line"]
    i["line"], i["lo"], i["hi"] = (i["line"] + d, max(1, i["lo"] + d),
                                   min(len(head), i["hi"] + d))
    return True


def advance(issues: List[dict], u0: Optional[Dict[str, dict]], blobs: Dict[str, List[str]],
            old: Optional[Dict[str, List[str]]] = None) -> Tuple[List[dict], bool]:
    """Move every issue to the new head. `u0` is `regions.parse_u0` of the diff between the
    previously reviewed head and this one, or None when that head is unknown: the issues are
    then relocated by anchor (the second value is True) or keep their region. An open issue
    whose whole region was deleted is searched in its file, by its lines and then by the
    window of its primary line (`_relocate`: `old` holds the blobs at the previous head,
    `blobs` those at this one, see `moved_lookups`), and is an orphan when both are gone. A closed issue is never searched: a waiver does not transfer."""
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
        if mapped is not None:
            i["line"], i["lo"], i["hi"] = mapped
        elif _closed(i) or not _relocate(i, (old or {}).get(src["file"]),
                                         blobs.get(entry["path"])):
            i["orphan"] = True
    return out, u0 is None


def _dist(i: dict, line: int) -> int:
    lo, hi = i.get("lo", i["line"]), i.get("hi", i["line"])
    return lo - line if line < lo else max(0, line - hi)


def _closed(i: dict) -> bool:
    return i.get("status", "open") in CLOSED


def _rank(x: dict) -> int:
    return SEV_RANK.get(x.get("severity"), len(SEV_RANK))


def _source(i: dict) -> str:
    """The issue a family is named after: `via` (set when a more severe `same_as` created the
    issue), else the issue itself."""
    return i.get("via") or i["id"]


def _family(t: dict, issues: List[dict], path: str) -> List[dict]:
    """`t` and every non-orphan issue, open or closed, with the same source in `t`'s file, or
    in `path` (the finding's file) when `t` is an orphan: an orphan has no region, so the
    issues a `same_as` to it created live where the model reported them."""
    s, where = _source(t), path if t.get("orphan") else t["file"]
    return [t] + [i for i in issues if i is not t and not i.get("orphan")
                  and i["file"] == where and _source(i) == s]


def _same_as(f: dict, ref: dict,
             issues: List[dict]) -> Tuple[Optional[dict], Optional[str], bool]:
    """`(merge, via, valid)` for a finding naming `ref` in `same_as`. The candidates are `ref`
    when it is an orphan or within SAME_AS lines of the finding (in its file), and the
    members of its family within SAME_AS lines of the finding; the `same_as` is valid when
    there is one. `merge` is `ref` when it is a candidate at least as severe as the finding,
    else the most severe candidate (nearest first on ties) when it is at least as severe;
    otherwise None: a far member never takes the report, so it cannot hide a defect the
    model tied to a nearby or orphan issue. With `merge` None, `via` is the family's source
    when the finding is to join the family if it becomes new: always when valid (it is then
    more severe than every candidate), and, when it is out of range in `ref`'s file, if it
    is at least as severe as every member (a report drifting from the family; a less severe
    one would make the family reach further for a defect it does not cover)."""
    far = float("inf")

    def dist(i: dict) -> float:
        return far if i.get("orphan") or i["file"] != f["file"] else _dist(i, f["line"])
    fam = _family(ref, issues, f["file"])
    cands = [i for i in fam if dist(i) <= SAME_AS or (i is ref and ref.get("orphan"))]
    if ref in cands and _rank(ref) <= _rank(f):
        return ref, None, True
    if cands:
        top = min(cands, key=lambda i: (_rank(i), dist(i)))
        if _rank(top) <= _rank(f):
            return top, None, True
        return None, _source(ref), True
    if f["file"] == ref["file"] and all(_rank(f) <= _rank(i) for i in fam):
        return None, _source(ref), False
    return None, None, False


def classify(findings: List[dict], issues: List[dict]) -> Tuple[List[dict], List[dict], List[dict]]:
    """Split `findings` into `(new, duplicates, links)`. A finding naming an issue T in
    `same_as` (valid for an orphan anywhere, else in T's file within SAME_AS lines of the
    nearest issue of T's family, see `_same_as`) is not new when an issue M in reach (T, or
    a family member within SAME_AS lines of it) is at least as severe as it: a closed M
    swallows it as a duplicate (`dup_of` set), an open one as a link (which carries the
    finding's `blocking`). A finding more severe than every issue in reach has its
    `same_as` ignored (`same_as_ignored` names T) and, when it becomes new, joins the family
    (`via`). Otherwise an exact re-report of an open non-orphan issue (same file, line and
    title, not more severe) is a link to it, blocking or not: a link keeps the issue open, so
    it cannot lower the verdict (a closed issue gets no such match). Otherwise position decides, from the non-orphan issues within NEAR lines of it
    (position never matches an orphan). Position only annotates a blocking finding: it is
    always new, with `near_issue` (open) or `near_closed` (resolved or waived) naming the
    nearest of them (an open one on ties), and keeps its `blocking` and `needs_check`; only the model's `same_as`
    can merge it. A non-blocking finding, which cannot change the verdict:
    - strictly more severe than every one of them: a new issue with `near_issue` naming the
      nearest one;
    - else, with an open issue among them: a duplicate of the nearest open one, marked
      `folded` (merged by position, so the body shows it; a `same_as` duplicate or link is not);
    - else (only closed ones): a new issue with `near_closed` naming the nearest one.
    An open issue reported resolved in this run still counts as open here."""
    by_id = {i["id"]: i for i in issues}
    new, dups, links = [], [], []
    for f in findings:
        ref = by_id.get(f.get("same_as"))
        via = None
        if ref is not None:
            top, via, valid = _same_as(f, ref, issues)
            if top is not None:
                if _closed(top):
                    dups.append(dict(f, dup_of=top["id"]))
                else:
                    links.append({"id": top["id"], "file": f["file"], "line": f["line"],
                                  "blocking": bool(f.get("blocking"))})
                continue
            if valid:
                f = dict(f, same_as_ignored=ref["id"])
        same = next((i for i in issues if not i.get("orphan") and not _closed(i)
                     and i["file"] == f["file"] and i["line"] == f["line"]
                     and i.get("title") == f.get("title") and _rank(f) >= _rank(i)), None)
        if same is not None:
            links.append({"id": same["id"], "file": f["file"], "line": f["line"],
                          "blocking": bool(f.get("blocking"))})
            continue
        made = dict(f, via=via) if via else f
        near = [i for i in issues if not i.get("orphan") and i["file"] == f["file"]
                and _dist(i, f["line"]) <= NEAR]
        if not near:
            new.append(made)
            continue
        nearest = min(near, key=lambda i: (_dist(i, f["line"]), _closed(i)))  # open on ties
        note = "near_closed" if _closed(nearest) else "near_issue"
        opened = [i for i in near if not _closed(i)]
        if f.get("blocking"):
            new.append(dict(made, **{note: nearest["id"]}))
        elif all(_rank(f) < _rank(i) for i in near):
            new.append(dict(made, near_issue=nearest["id"]))
        elif opened:
            # merged by position only: the model did not say it is the same problem
            dups.append(dict(made, dup_of=min(opened, key=lambda i: _dist(i, f["line"]))["id"],
                             folded=True))
        else:
            new.append(dict(made, near_closed=nearest["id"]))
    return new, dups, links


def shown_lines(chunks: List[dict], recheck: Dict[int, List[dict]]) -> Dict[int, Dict[str, Set[int]]]:
    """Per chunk index and file, the line numbers the model saw: the right-side lines of the
    chunk diff plus the numbered lines of its recheck items (`start` is the first line of
    `text`) that were not cut (`cut`): the model did not see the rest of a cut line. A row
    the chunk diff holds cut (the chunk's `cut`, `diff_split.build_chunks`) is not shown by
    the diff."""
    out: Dict[int, Dict[str, Set[int]]] = {}
    for c in chunks:
        files = out.setdefault(c["index"], {})
        rows = c.get("cut") or {}
        for f in parse_unified_diff(c.get("diff") or ""):
            files.setdefault(f["path"], set()).update(
                n for n in f["right_lines"] if n not in rows.get(f["path"], ()))
        for item in recheck.get(c["index"], ()):
            n = len(item["text"].split("\n"))
            cut = set(item.get("cut") or ())
            files.setdefault(item["file"], set()).update(
                k for k in range(item["start"], item["start"] + n) if k not in cut)
    return out


def _window(lines: List[str], p: dict, context: int) -> Tuple[int, int, str, List[int]]:
    """`(lo, hi, text, cut)`: the region with `context` lines on each side, clipped to the
    file. A line of the region is cut to REGION_LINE_MAX characters, a context line to
    LINE_MAX (LINE_CUT marks it), so one line keeps one row; `cut` lists the cut lines."""
    lo = max(1, p.get("lo", p["line"]) - context)
    hi = min(len(lines), p.get("hi", p["line"]) + context)
    rlo, rhi = p.get("lo", p["line"]), p.get("hi", p["line"])
    rows, cut = [], []
    for n in range(lo, hi + 1):
        t, room = lines[n - 1], REGION_LINE_MAX if rlo <= n <= rhi else LINE_MAX
        if len(t) > room:
            t = t[:room] + LINE_CUT
            cut.append(n)
        rows.append(t)
    return lo, hi, "\n".join(rows), cut


def assign_recheck(chunks: List[dict], previous: List[dict], blobs: Dict[str, List[str]],
                   cfg: dict) -> Tuple[Dict[int, List[dict]], List[str]]:
    """Give each issue marked `recheck` (and `pre == "ask"`, not an orphan) the head lines of
    its region with CONTEXT lines on each side, in the first chunk that holds its file.
    Items are `{"id", "file", "lo", "hi", "start", "text", "cut", "region_cut"}` (`lo`..`hi`
    is the shown range, `start == lo`, `text` has no trailing newline, `cut` the lines cut,
    `region_cut` whether one of them is in the issue's region). Each chunk has its own budget
    of `recheck_max_chars` on top of its diff. A region line over REGION_LINE_MAX characters
    and a context line over LINE_MAX are cut (LINE_CUT marks it) before the budget check; a
    cut line does not count as shown (`shown_lines`), so an issue with a cut region line is
    never resolved by its recheck. The first item of a chunk that does not fit gets
    less context, down to none, and is served even when its region alone is over the budget.
    A later item that does not fit, or one whose file is in no chunk, is left out: the second
    value lists those ids. An issue whose file has no blob cannot be shown and is neither.
    Issues marked `pending` (left out by the previous run) are served first, the others keep
    their order, so a left-out issue gets an empty budget on the next run."""
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
        lo, hi, text, cut = _window(lines, p, CONTEXT)
        if holder is None or lo > hi:
            left.append(p["id"])
            continue
        if not used.get(holder):
            context = CONTEXT
            while len(text) > budget and context > 0:
                context -= 1
                lo, hi, text, cut = _window(lines, p, context)
        elif used[holder] + len(text) > budget:
            left.append(p["id"])
            continue
        used[holder] = used.get(holder, 0) + len(text)
        rlo, rhi = p.get("lo", p["line"]), p.get("hi", p["line"])
        by_chunk.setdefault(holder, []).append(
            {"id": p["id"], "file": p["file"], "lo": lo, "hi": hi, "start": lo, "text": text,
             "cut": cut, "region_cut": any(rlo <= n <= rhi for n in cut)})
    return by_chunk, left


def pending_after(recheck: Dict[int, List[dict]], left_out: List[str], results: List[dict],
                  chunks: List[dict], skipped: Iterable[str] = (),
                  truncated: Iterable[str] = (), recheck_only: Iterable[str] = ()) -> List[str]:
    """Ids still to be rechecked: the ones left out, the ones whose chunk is not `ok`, and the
    ones whose `resolved` report is refused because of their file: it is in any chunk that is
    not `ok`, or in `skipped` or `truncated` (the file lists of `build_chunks`) unless it is in
    `recheck_only` (a target only for rechecks: `aggregate._previous` does not refuse for
    that). An item with a cut region line is pending only by these rules: no later run can
    show more of the line."""
    status = {r["index"]: r.get("status") for r in results}
    bad = {p for c in chunks if status.get(c["index"]) != "ok" for p in c["files"]}
    bad |= (set(skipped) | set(truncated)) - set(recheck_only)
    out = list(left_out)
    for index in sorted(recheck):
        out += [i["id"] for i in recheck[index] if i["id"] not in out
                and (status.get(index) != "ok" or i.get("file") in bad)]
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


def apply_waivers(state: Optional[dict], ids: Set[str]) -> Optional[dict]:
    """A copy of a normalized `state` in which every open issue named in `ids` is `waived`.
    `waived_now` counts those changes, so applying the same ids again gives 0. A closed issue
    stays as it is and ids that are not in the ledger are ignored."""
    if state is None:
        return None
    n = 0
    issues = []
    for i in state.get("issues") or []:
        if i["id"] in ids and i.get("status", "open") not in CLOSED:
            i = dict(i, status="waived")
            n += 1
        issues.append(i)
    return dict(state, issues=issues, waived_now=n)
