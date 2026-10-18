"""Merge chunk findings and compute the deterministic review verdict."""
from __future__ import annotations

import hashlib
import re
from typing import Dict, List, Optional, Set, Tuple

SEV_RANK = {"critical": 0, "major": 1, "minor": 2, "info": 3}
STATUS_RANK = {"open": 0, "unclear": 1, "resolved": 2}
_PREFIX = re.compile(r"^(?:\./|a/|b/|/)")


def finding_id(f: dict) -> str:
    key = "%s:%s:%s" % (f.get("file"), f.get("line"), f.get("title"))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]


def normalize_path(p: str) -> str:
    return _PREFIX.sub("", p)


def _sev(f: dict) -> int:
    return SEV_RANK.get(f.get("severity"), len(SEV_RANK))


def _strength(f: dict) -> Tuple[int, int]:
    return (_sev(f), -f.get("confidence", 0))


def _collect(results: List[dict], cfg: dict, changed: Optional[Dict[str, Set[int]]]):
    kept, dropped = [], 0
    for r in results:
        if r.get("status") != "ok" or not r.get("output"):
            continue
        for raw in r["output"].get("findings", []):
            f = dict(raw)
            f["file"] = normalize_path(f["file"])
            if f.get("confidence", 0) < cfg["min_confidence"]:
                dropped += 1
            elif changed is not None and f.get("line") not in changed.get(f["file"], set()):
                dropped += 1
            else:
                kept.append(f)
    return kept, dropped


def _dedupe(findings: List[dict]) -> List[dict]:
    out: List[dict] = []
    for f in findings:
        for i, g in enumerate(out):
            if (g["file"] == f["file"] and g.get("category") == f.get("category")
                    and abs(g["line"] - f["line"]) <= 2):
                if _strength(f) < _strength(g):  # ties keep the earlier one
                    out[i] = f
                break
        else:
            out.append(f)
    return out


def _previous(results: List[dict], previous: List[dict], delta_files: Optional[Set[str]]):
    reported: Dict[str, Tuple[str, Optional[str]]] = {}
    for r in results:
        if r.get("status") != "ok" or not r.get("output"):
            continue
        for p in r["output"].get("previous_findings", []):
            cur = reported.get(p["id"])
            if cur is None or STATUS_RANK[p["status"]] < STATUS_RANK[cur[0]]:
                reported[p["id"]] = (p["status"], p.get("note"))
    touched = delta_files or set()
    out = []
    for prev in previous:
        e = dict(prev)
        if prev["id"] in reported:
            e["status"] = reported[prev["id"]][0]
            if reported[prev["id"]][1] is not None:
                e["note"] = reported[prev["id"]][1]
        else:
            e["status"] = "unclear" if prev["file"] in touched else "open"
        out.append(e)
    return out


def _summaries(results: List[dict]) -> List[str]:
    out = []
    for r in sorted(results, key=lambda x: x["index"]):
        if r.get("status") != "ok" or not r.get("output"):
            continue
        o = r["output"]
        line = "[c%d] %s" % (r["index"] + 1, o.get("summary", ""))
        if o.get("incomplete_reason"):
            line += " (미완료: %s)" % o["incomplete_reason"]
        out.append(line)
    return out


def merge(results, cfg, previous, changed, delta_files, no_targets):
    kept, dropped = _collect(results, cfg, changed)
    findings = _dedupe(kept)
    for f in findings:
        in_block = f.get("severity") in cfg["block_severities"]
        f["id"] = finding_id(f)
        f["blocking"] = in_block and f["confidence"] >= cfg["block_confidence"]
        f["needs_check"] = in_block and not f["blocking"]
    prev_ids = {p["id"] for p in previous}
    findings = [f for f in findings if f["id"] not in prev_ids]
    prev_out = _previous(results, previous, delta_files)
    incomplete = sorted(r["index"] for r in results if r.get("status") != "ok")
    failed_all = bool(results) and all(r.get("status") == "failed" for r in results)
    blocking = any(f["blocking"] for f in findings) or any(
        p.get("blocking") and p["status"] == "open" for p in prev_out)
    if blocking:
        event = "REQUEST_CHANGES"
    elif no_targets or incomplete or failed_all:
        event = "COMMENT"
    else:
        event = "APPROVE"
    findings.sort(key=lambda f: (_sev(f), -f["confidence"], f["file"], f["line"]))
    return {"event": event, "findings": findings, "previous": prev_out,
            "incomplete": incomplete, "failed_all": failed_all,
            "no_targets": no_targets, "summaries": _summaries(results),
            "dropped": dropped}


def split_inline(findings, right_lines):
    inline, body = [], []
    for f in findings:
        (inline if f["line"] in right_lines.get(f["file"], ()) else body).append(f)
    return inline, body
