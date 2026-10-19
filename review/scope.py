"""Review scope (full / incremental / unchanged) and small CLI helpers."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from review.diff_split import line_map, parse_unified_diff
from review.gitutil import diff_text, is_ancestor
from review.render import render_step_summary

STATE_KEYS = ("id", "file", "line", "title", "severity", "blocking")


def determine(repo: str, base: str, head: str, prev: Optional[dict]) -> Dict[str, Any]:
    pr_files = parse_unified_diff(diff_text(repo, base, head, merge_base=True))
    psha = (prev or {}).get("sha") or None
    out: Dict[str, Any] = {"pr_files": pr_files, "changed": None, "delta_files": None}
    if psha == head:
        out.update(mode="unchanged", range="%s..%s" % (psha[:7], head[:7]), targets=[])
    elif psha and is_ancestor(repo, psha, head):
        # A merge of base into the PR branch brings upstream changes into psha..head:
        # keep only files and added lines that are also part of the PR diff itself.
        pr_added = line_map(pr_files, "added_lines")
        targets = [f for f in parse_unified_diff(diff_text(repo, psha, head, merge_base=False))
                   if f["path"] in pr_added]
        changed = {p: lines & pr_added[p] for p, lines in line_map(targets, "added_lines").items()}
        out.update(mode="incremental", range="%s..%s" % (psha[:7], head[:7]), targets=targets,
                   changed=changed, delta_files={f["path"] for f in targets})
    else:  # first review, or force-push/rebase: review the whole PR again
        out.update(mode="full", range="%s...%s" % (base[:7], head[:7]), targets=pr_files)
        if psha:
            out["delta_files"] = {f["path"] for f in pr_files}
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
    if not path:
        return None
    with open(path, "r", encoding="utf-8") as f:
        state = json.load(f)
    return state if isinstance(state, dict) else None


def next_state(head: str, review: dict, prev: Optional[dict]) -> dict:
    """State for the posted review: new findings + carried open/unclear previous ones.
    If the review is not complete (failed, partial or skipped), the previous sha (or "") is
    kept instead of head, so the next run re-reviews the code that was never covered."""
    sha = head
    if not review["complete"]:
        sha = (prev or {}).get("sha") or ""
    keep = [p for p in review.get("previous", []) if p.get("status") in ("open", "unclear")]
    return {"sha": sha, "findings": [{k: f.get(k) for k in STATE_KEYS}
                                     for f in list(review["findings"]) + keep]}


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
