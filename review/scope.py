"""Review scope (full / incremental / unchanged) and small CLI helpers."""
from __future__ import annotations

import json
import os
from collections import Counter
from typing import Any, Dict, List, Optional, Set

from review.diff_split import line_map, parse_unified_diff, reviewable
from review.fingerprint import decode_seen, encode_seen, whole_hash
from review.gitutil import diff_text, read_blobs, renames_between
from review.hunks import near_hunks, select_file
from review.previous import relocate
from review.render import render_step_summary
from review.seen import update_seen
from review.state_fit import fit_state
from review.validate import normalize_state

STATE_KEYS = ("id", "file", "line", "anchor", "title", "severity", "blocking")


def _path_map(repo: str, prev: Optional[dict], head: str, pr_files: List[dict],
              findings: List[dict]) -> Dict[str, str]:
    """State path -> head path: renames since the previous head, then base -> head."""
    base_ren = {f["old_path"]: f["path"] for f in pr_files if f["old_path"]}
    prev_ren = renames_between(repo, prev["sha"], head) if prev else {}
    out = {}
    for p in list((prev or {}).get("seen") or {}) + list((prev or {}).get("seen_files") or {}) \
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
                           "prev_seen": {}, "previous": []}
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
    alias = {names[k]: k for k in list(seen) + list(whole) if names.get(k, k) != k}
    limit = int(cfg["chunk_max_tokens"] * 3.5)
    targets: List[dict] = []
    for f in rev:
        path, old = f["path"], f["old_path"]
        s = seen.get(path) or seen.get(alias.get(path)) or seen.get(old) or Counter()
        w = whole.get(path) or whole.get(alias.get(path)) or whole.get(old)
        sel = select_file(f, blobs.get(path, []), None if prev is None else s, w,
                          limit_chars=limit)
        out["head_prints"][path] = sel["head"]
        out["prev_seen"][path] = sel["head"] if w is not None and w == whole_hash(
            sel["head"]) else s
        if sel["file"] is not None:
            targets.append(sel["file"])
            out["added"][path], out["deleted"][path] = sel["added"], sel["deleted"]
    tpaths = {t["path"] for t in targets}
    previous = relocate(findings, live, blobs, tpaths, names, gone, [f["path"] for f in rev])
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


def parse_overrides(items: Optional[List[str]]) -> Dict[str, Any]:
    """`key=value` pairs; value is JSON, falling back to the raw string."""
    out: Dict[str, Any] = {}
    for item in items or []:
        key, sep, raw = item.partition("=")
        if not sep or not key:
            raise ValueError("--set expects key=value: %r" % item)
        try:
            out[key] = json.loads(raw)
        except ValueError:
            out[key] = raw
    return out


def read_state_file(path: Optional[str]) -> Optional[dict]:
    """Dry-run previous state; normalized like the online marker (v1 -> None)."""
    if not path:
        return None
    with open(path, "r", encoding="utf-8") as f:
        return normalize_state(json.load(f))


def next_state(head: str, review: dict, scope: dict, chunks: List[dict],
               results: List[dict]) -> dict:
    """State v2 for the posted review: sha is always head; `complete` tells the next run
    whether the same head still needs a review. The seen-set only grows by lines that
    went through an `ok` chunk."""
    seen, seen_files = encode_seen(update_seen(scope, chunks, results))
    keep = [p for p in review.get("previous", []) if p.get("status") in ("open", "unclear")]
    findings = [dict({k: f.get(k) for k in STATE_KEYS}, anchor=f.get("anchor") or "")
                for f in list(review["findings"]) + keep]
    return fit_state({"v": 2, "sha": head, "complete": bool(review["complete"]), "seen": seen,
                      "seen_files": seen_files, "findings": findings})


def reviewed_files(chunks: List[dict]) -> List[str]:
    out: List[str] = []
    for c in chunks:
        out.extend(p for p in c["files"] if p not in out)
    return out


def append_step_summary(env: Dict[str, str], results: List[dict]) -> None:
    path = env.get("GITHUB_STEP_SUMMARY")
    if path and results:
        with open(path, "a", encoding="utf-8") as f:
            f.write("### AI review chunks\n\n" + render_step_summary(results) + "\n")


def default_out_dir(env: Dict[str, str]) -> str:
    rt = env.get("RUNNER_TEMP")
    return os.path.join(rt, "ai-review") if rt else os.path.abspath(".ai-review-out")
