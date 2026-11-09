"""Review scope (full / incremental / unchanged) and the next state."""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional, Set

from review.diff_split import line_map, parse_unified_diff, reviewable
from review.fingerprint import decode_hunks, decode_seen, encode_seen, pack, unpack, whole_hash
from review.gitutil import diff_text, diff_u0, read_blobs, renames_between
from review.hunks import cut_prints, near_hunks, select_file
from review.ledger import CLOSED, advance, moved_lookups, new_issue
from review.previous import _closest, annotate
from review.regions import parse_u0
from review.seen import update_hunks, update_seen
from review.state_fit import fit_state

ISSUE_KEYS = ("id", "file", "line", "lo", "hi", "status", "severity", "blocking", "anchor")


def _path_map(repo: str, prev: Optional[dict], head: str, pr_files: List[dict],
              issues: List[dict]) -> Dict[str, str]:
    """State path -> head path: renames since the previous head, then base -> head."""
    base_ren = {f["old_path"]: f["path"] for f in pr_files if f["old_path"]}
    prev_ren = renames_between(repo, prev["sha"], head) if prev else {}
    out = {}
    st = prev or {}
    for p in [k for key in ("seen", "seen_files", "seen_hunks", "cut")
              for k in st.get(key) or {}] \
            + [e["file"] for e in issues]:
        q = prev_ren.get(p, p)
        out[p] = base_ren.get(q, q)
    return out


def _advance(repo: str, head: str, prev: Optional[dict], issues: List[dict],
             names: Dict[str, str]):
    """The ledger issues moved to `head`: `(issues, weak)`. `weak` is True when the previous
    head is not available locally, so the issues were located by anchor instead of by diff;
    their files are first mapped through `names` (the renames of the PR itself). With a diff,
    only the files of open issues whose region was deleted are read, at both heads, to search
    for moved lines; a file missing at either head is simply not searched."""
    if prev is None:
        return [], False
    text = diff_u0(repo, prev["sha"], head)
    u0 = None if text is None else parse_u0(text)
    if u0 is None:
        issues = [dict(i, file=names.get(i["file"], i["file"])) for i in issues]
        return advance(issues, u0, read_blobs(repo, head, sorted({i["file"] for i in issues})))
    look = moved_lookups(issues, u0)
    return advance(issues, u0, read_blobs(repo, head, sorted({new for _, new in look})),
                   read_blobs(repo, prev["sha"], sorted({old for old, _ in look})))


def _no_hunks(f: dict) -> bool:
    """A PR file whose diff has no hunk: a pure rename, a mode change, or a change the PR
    reverted. A binary or deleted file is not one."""
    return (f["status"] != "D" and not f["binary"]
            and re.search(r"(?m)^@@ ", f["text"]) is None)


def determine(repo: str, base: str, head: str, prev: Optional[dict], cfg: dict) -> Dict[str, Any]:
    """Scope by line content: only hunks with fingerprints not yet in the seen-set are targets.
    The previous ledger is moved to `head` first (`ledger.advance`): `previous` holds the open
    issues, `closed` the closed ones. `prev_cut` holds the stored prints of lines too long for
    a chunk that are still in the head (by head path, renames followed like the seen-set),
    `cut_files` their files: a gap that never approves (`aggregate.merge`)."""
    pr_files = parse_unified_diff(diff_text(repo, base, head, merge_base=True))
    out: Dict[str, Any] = {"mode": "unchanged", "range": "%s...%s" % (base[:7], head[:7]),
                           "targets": [], "pr_files": pr_files, "reviewable": 0, "changed": {},
                           "added": {}, "deleted": {}, "blobs": {}, "head_prints": {},
                           "prev_seen": {}, "previous": [], "hunk_keys": {}, "prior": {},
                           "prev_hunks": {}, "closed": [], "weak_tracking": False, "prev": prev,
                           "dropped_closed": int((prev or {}).get("dropped_closed") or 0),
                           "prev_cut": {}, "cut_files": sorted((prev or {}).get("cut") or {})}
    pending = list((prev or {}).get("recheck_pending") or [])
    settle = bool(prev and prev["sha"] == head and prev["complete"] and not pending)
    if settle and not prev.get("waived_now"):
        return out
    live = {f["path"] for f in pr_files if f["status"] != "D"}
    reverted = {f["path"] for f in pr_files if _no_hunks(f)}
    gone = {f["path"] for f in pr_files if f["status"] == "D"}
    rev = [f for f in pr_files if reviewable(f, cfg)]
    stored = (prev or {}).get("issues") or []
    names = {} if settle else _path_map(repo, prev, head, pr_files, stored)
    moved, weak = (stored, False) if settle else _advance(repo, head, prev, stored, names)
    opened = [i for i in moved if i["status"] not in CLOSED]
    closed = [dict(i, orphan=True) if i["file"] not in live else i
              for i in moved if i["status"] in CLOSED]
    if settle:  # a new waiver on a head that was fully reviewed: only the verdict changes
        out.update(mode="settle", reviewable=len(rev), closed=closed,
                   previous=annotate(opened, live, set(), gone, reverted=reverted))
        return out
    want = [f["path"] for f in rev]
    want += [p for p in dict.fromkeys(i["file"] for i in opened) if p not in want]
    blobs = read_blobs(repo, head, want)
    seen, whole = decode_seen(prev)
    done = decode_hunks(prev)
    cuts = {p: unpack(v) for p, v in ((prev or {}).get("cut") or {}).items()}
    alias = {names[k]: k for k in list(seen) + list(whole) + list(done) + list(cuts)
             if names[k] != k}
    limit = int(cfg["chunk_max_tokens"] * 3.5)
    targets: List[dict] = []
    for f in rev:
        path, old = f["path"], f["old_path"]
        s = seen.get(path) or seen.get(alias.get(path)) or seen.get(old) or Counter()
        w = whole.get(path) or whole.get(alias.get(path)) or whole.get(old)
        h = done.get(path) or done.get(alias.get(path)) or Counter()
        sel = select_file(f, blobs.get(path, []), None if prev is None else s, w,
                          limit_chars=limit, seen_hunks=h)
        out["head_prints"][path] = sel["head"]
        cut = (cuts.get(path) or cuts.get(alias.get(path)) or cuts.get(old) or Counter()) \
            & sel["head"]
        if cut:
            out["prev_cut"][path] = cut
        out["hunk_keys"][path], out["prior"][path], out["prev_hunks"][path] = (
            sel["keys"], sel["prior"], set(h) if prev is not None else set())
        out["prev_seen"][path] = sel["head"] if w is not None and w == whole_hash(
            sel["head"]) else s
        if sel["file"] is not None:
            targets.append(sel["file"])
            out["added"][path], out["deleted"][path] = sel["added"], sel["deleted"]
    tpaths = {t["path"] for t in targets}
    lost = {i["id"] for i in opened if weak and not i["orphan"]
            and _closest(blobs.get(i["file"], []), i.get("anchor") or "", i["line"]) is None}
    previous = annotate(opened, live, tpaths, gone, lost, weak, reverted)
    changed = line_map(targets, "right_lines") if prev is None else {
        p: out["added"][p] | out["deleted"][p] for p in tpaths}
    ask = {i["id"] for i in previous} if prev and prev["sha"] != head else set(pending)
    targets += _recheck(previous, rev, tpaths, limit, ask)
    for p in previous:  # left out by the last run: first in line for the recheck budget
        p["pending"] = p["id"] in pending
    if prev is None:
        mode = "full"
    elif not targets and not pending and not (prev or {}).get("waived_now") and not any(
            p["pre"] in ("resolved", "orphan") or p.get("recheck") for p in previous):
        mode = "unchanged"
    else:
        mode = "incremental"
    out.update(mode=mode, targets=targets, reviewable=len(rev), blobs=blobs, previous=previous,
               changed=changed, closed=closed, weak_tracking=weak,
               cut_files=sorted(out["prev_cut"]))
    return out


def _recheck(previous: List[dict], rev: List[dict], tpaths: Set[str], limit: int,
             ask: Set[str]) -> List[dict]:
    """Every open issue in `ask`, and every one that could not be located (weak tracking), in a
    PR file the run can review is marked `recheck` and `pre="ask"`: the model is asked about it
    with the lines around its region (`ledger.assign_recheck`). A file without new lines
    gets the hunks nearest the issues as targets, marked `recheck_only` (none of their lines
    is in `changed`); a file that is a target already needs no new one. Orphans and issues of
    files that left the PR or that the PR no longer changes (no hunk, `annotate`: resolved)
    are not rechecked."""
    want: Dict[str, List[dict]] = {}
    for p in previous:
        if p["pre"] in ("open", "ask") and (p["id"] in ask or not p["located"]):
            want.setdefault(p["file"], []).append(p)
    out = []
    for f in rev:
        mine = want.get(f["path"], [])
        if mine and f["path"] not in tpaths:
            t = near_hunks(f, [p.get("line") or 0 for p in mine], limit)
            if t is not None:
                out.append(dict(t, recheck_only=True))
        for p in mine:
            p["pre"], p["recheck"] = "ask", True
    return out


def _record(i: dict) -> dict:
    """An issue as stored in the marker: identity always (`via` when set), display text and
    flags when set."""
    e = {k: i.get(k) for k in ISSUE_KEYS}
    e["lo"], e["hi"] = i.get("lo", i["line"]), i.get("hi", i["line"])
    e["anchor"] = i.get("anchor") or ""
    for k in ("title", "excerpt"):
        if i.get(k):
            e[k] = i[k]
    if i.get("orphan"):
        e["orphan"] = True
    if i.get("via"):
        e["via"] = i["via"]
    return e


def _relink(closed: List[dict], review: dict) -> List[dict]:
    """A closed orphan that the model pointed at with `same_as` gets a region at the finding.
    Such a finding is a duplicate of the closed issue (`dup_of`); a link entry counts too.
    The issue stays closed."""
    at: Dict[str, dict] = {}
    for x in review.get("links") or []:
        at.setdefault(x["id"], x)
    for x in review.get("duplicates") or []:
        at.setdefault(x["dup_of"], x)
    out = []
    for c in closed:
        x = at.get(c["id"])
        if x is not None and c.get("orphan"):
            c = dict(c, file=x["file"], line=x["line"], lo=x["line"], hi=x["line"], orphan=False)
        out.append(c)
    return out


def _issues(review: dict, scope: dict) -> List[dict]:
    """Issues newest first: this run's new ones, the previous open ones (`unclear` is a per-run
    outcome and is stored as `open`), the ones closed by this run, then the earlier closed ones."""
    prev = review.get("previous", [])
    keep = [dict(p, status="open") for p in prev if p.get("status") in ("open", "unclear")]
    shut = [dict(p, orphan=p.get("orphan") or p.get("pre") == "resolved")
            for p in prev if p.get("status") in CLOSED]
    return [new_issue(f) for f in review["findings"]] + keep + shut \
        + _relink(scope.get("closed") or [], review)


def _cut(scope: dict, chunks: List[dict], results: List[dict]) -> Dict[str, str]:
    """`cut` of the next state: the previous cut prints still in the head (`prev_cut`), and
    the prints of the rows cut in this run's `ok` chunks (a row cut in another chunk is not
    seen: it is reviewed again). Packed per file, only prints of the head."""
    status = {r["index"]: r.get("status") for r in results}
    out: Dict[str, Counter] = {p: Counter(c) for p, c in (scope.get("prev_cut") or {}).items()}
    for c in chunks:
        if status.get(c["index"]) == "ok":
            for path, prints in cut_prints(c, scope["blobs"]).items():
                out[path] = out.get(path, Counter()) | prints
    heads = scope["head_prints"]
    out = {p: c & heads.get(p, Counter()) for p, c in out.items()}
    return {p: pack(c) for p, c in sorted(out.items()) if c}


def next_state(head: str, review: dict, scope: dict, chunks: List[dict],
               results: List[dict]) -> dict:
    """State v3 for the posted review: sha is always head; `complete` tells the next run
    whether the same head still needs a review. The seen-set only grows by lines that
    went through an `ok` chunk; `cut` (when not empty) keeps the prints of lines too long
    for a chunk while they are in the head (`_cut`). Issues are newest first: this run's new
    ones, the previous open ones (`unclear` is a per-run outcome and is stored as `open`), the
    ones closed by this run, then the earlier closed ones."""
    seen = update_seen(scope, chunks, results)
    hunks = update_hunks(scope, chunks, results, seen)
    full = {p for p, c in scope["head_prints"].items() if seen.get(p, Counter()) == c}
    seen_c, seen_files = encode_seen(seen, full=full)
    issues = _issues(review, scope)
    state = {"v": 3, "sha": head, "complete": bool(review["complete"]), "seen": seen_c,
             "seen_files": seen_files,
             "seen_hunks": {p: pack(Counter(k)) for p, k in sorted(hunks.items())},
             "issues": [_record(i) for i in issues], "closed": {},
             "recheck_pending": list(review.get("recheck_pending") or [])}
    if scope.get("dropped_closed"):
        state["dropped_closed"] = scope["dropped_closed"]
    cut = _cut(scope, chunks, results)
    if cut:
        state["cut"] = cut
    return fit_state(state, partial=set(scope["head_prints"]) - full)


def settle_state(head: str, review: dict, scope: dict) -> dict:
    """State v3 for a settle run (no model call): the seen data and `recheck_pending` are those
    of `scope["prev"]` (the state after the waivers), and so is `cut`; the issues are the ledger after them, and
    the head counts as fully reviewed."""
    prev = scope["prev"]
    state = {"v": 3, "sha": head, "complete": True, "seen": dict(prev["seen"]),
             "seen_files": dict(prev["seen_files"]), "seen_hunks": dict(prev["seen_hunks"]),
             "issues": [_record(i) for i in _issues(review, scope)], "closed": {},
             "recheck_pending": list(prev.get("recheck_pending") or [])}
    if scope.get("dropped_closed"):
        state["dropped_closed"] = scope["dropped_closed"]
    if prev.get("cut"):
        state["cut"] = dict(prev["cut"])
    return fit_state(state, partial=set())
