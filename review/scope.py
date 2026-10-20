"""Review scope (full / incremental / unchanged) and the next state."""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Set

from review.diff_split import line_map, parse_unified_diff, reviewable
from review.fingerprint import decode_hunks, decode_seen, encode_seen, pack, whole_hash
from review.gitutil import diff_text, read_blobs, renames_between
from review.hunks import near_hunks, select_file
from review.previous import relocate
from review.seen import update_hunks, update_seen
from review.state_fit import fit_state

STATE_KEYS = ("id", "file", "line", "anchor", "title", "severity", "blocking")


def _path_map(repo: str, prev: Optional[dict], head: str, pr_files: List[dict],
              findings: List[dict]) -> Dict[str, str]:
    """State path -> head path: renames since the previous head, then base -> head."""
    base_ren = {f["old_path"]: f["path"] for f in pr_files if f["old_path"]}
    prev_ren = renames_between(repo, prev["sha"], head) if prev else {}
    out = {}
    st = prev or {}
    for p in [k for key in ("seen", "seen_files", "seen_hunks") for k in st.get(key) or {}] \
            + [e["file"] for e in findings]:
        q = prev_ren.get(p, p)
        out[p] = base_ren.get(q, q)
    return out


def determine(repo: str, base: str, head: str, prev: Optional[dict], cfg: dict) -> Dict[str, Any]:
    """Scope by line content: only hunks with fingerprints not yet in the seen-set are targets."""
    pr_files = parse_unified_diff(diff_text(repo, base, head, merge_base=True))
    out: Dict[str, Any] = {"mode": "unchanged", "range": "%s...%s" % (base[:7], head[:7]),
                           "targets": [], "pr_files": pr_files, "reviewable": 0, "changed": {},
                           "added": {}, "deleted": {}, "blobs": {}, "head_prints": {},
                           "prev_seen": {}, "previous": [], "hunk_keys": {}, "prior": {},
                           "prev_hunks": {}}
    if prev and prev["sha"] == head and prev["complete"]:
        return out
    live = {f["path"] for f in pr_files if f["status"] != "D"}
    gone = {f["path"] for f in pr_files if f["status"] == "D"}
    rev = [f for f in pr_files if reviewable(f, cfg)]
    findings = (prev or {}).get("findings") or []
    names = _path_map(repo, prev, head, pr_files, findings)
    want = [f["path"] for f in rev]
    want += [p for p in dict.fromkeys(names[e["file"]] for e in findings) if p not in want]
    blobs = read_blobs(repo, head, want)
    seen, whole = decode_seen(prev)
    done = decode_hunks(prev)
    alias = {names[k]: k for k in list(seen) + list(whole) + list(done) if names[k] != k}
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
        out["hunk_keys"][path], out["prior"][path], out["prev_hunks"][path] = (
            sel["keys"], sel["prior"], set(h) if prev is not None else set())
        out["prev_seen"][path] = sel["head"] if w is not None and w == whole_hash(
            sel["head"]) else s
        if sel["file"] is not None:
            targets.append(sel["file"])
            out["added"][path], out["deleted"][path] = sel["added"], sel["deleted"]
    tpaths = {t["path"] for t in targets}
    sblobs = blobs
    search = [f["path"] for f in rev]
    if any(names[e["file"]] not in live and names[e["file"]] not in blobs for e in findings):
        # the flagged code may have moved into any live file, reviewed or not (vendor/...)
        search += [f["path"] for f in pr_files if f["status"] != "D" and not f["binary"]
                   and f["path"] not in search]
        sblobs = dict(blobs, **read_blobs(repo, head, [p for p in search if p not in blobs]))
    previous = relocate(findings, live, sblobs, tpaths, names, gone, search)
    changed = line_map(targets, "right_lines") if prev is None else {
        p: out["added"][p] | out["deleted"][p] for p in tpaths}
    targets += _recheck(previous, rev, tpaths, limit)
    if prev is None:
        mode = "full"
    elif not targets and not any(p["pre"] == "resolved" for p in previous):
        mode = "unchanged"
    else:
        mode = "incremental"
    out.update(mode=mode, targets=targets, reviewable=len(rev), blobs=blobs, previous=previous,
               changed=changed)
    return out


def _recheck(previous: List[dict], rev: List[dict], tpaths: Set[str], limit: int) -> List[dict]:
    """A lost previous finding in a PR file without new lines: the hunks nearest its old
    line become targets (none of their lines is in `changed`) and the model is asked."""
    lost: Dict[str, List[dict]] = {}
    for p in previous:
        if p["pre"] == "open" and not p["located"] and p["file"] not in tpaths:
            lost.setdefault(p["file"], []).append(p)
    out = []
    for f in rev:
        t = near_hunks(f, [p.get("line") or 0 for p in lost.get(f["path"], [])], limit)
        if t is not None:
            out.append(t)
            for p in lost[f["path"]]:
                p["pre"] = "ask"
    return out


def next_state(head: str, review: dict, scope: dict, chunks: List[dict],
               results: List[dict]) -> dict:
    """State v2 for the posted review: sha is always head; `complete` tells the next run
    whether the same head still needs a review. The seen-set only grows by lines that
    went through an `ok` chunk."""
    seen = update_seen(scope, chunks, results)
    hunks = update_hunks(scope, chunks, results, seen)
    full = {p for p, c in scope["head_prints"].items() if seen.get(p, Counter()) == c}
    seen_c, seen_files = encode_seen(seen, full=full)
    keep = [p for p in review.get("previous", []) if p.get("status") in ("open", "unclear")]
    findings = [dict({k: f.get(k) for k in STATE_KEYS}, anchor=f.get("anchor") or "")
                for f in list(review["findings"]) + keep]
    return fit_state({"v": 2, "sha": head, "complete": bool(review["complete"]), "seen": seen_c,
                      "seen_files": seen_files,
                      "seen_hunks": {p: pack(Counter(k)) for p, k in sorted(hunks.items())},
                      "findings": findings}, partial=set(scope["head_prints"]) - full)
