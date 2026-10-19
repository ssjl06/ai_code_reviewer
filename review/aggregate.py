"""Merge chunk findings and compute the deterministic review verdict."""
from __future__ import annotations

import hashlib
import re
from typing import Dict, List, Optional, Set, Tuple

SEV_RANK = {"critical": 0, "major": 1, "minor": 2, "info": 3}
OK_STATUSES = ("ok", "partial")
STATUS_RANK = {"open": 0, "unclear": 1, "resolved": 2}
_PREFIX = re.compile(r"^(?:\./|a/|b/|/)")


def finding_id(f: dict) -> str:
    key = "%s:%s:%s" % (f.get("file"), f.get("anchor") or f.get("line"), f.get("title"))
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
        if r.get("status") not in OK_STATUSES or not r.get("output"):
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


def _resolve(prev: dict) -> str:
    """Status of a previous finding the model did not report, from `previous.relocate`."""
    pre = prev.get("pre")
    if pre == "resolved":
        return "resolved"
    if pre == "ask" and not prev.get("located"):
        return "unclear"
    return "open"


def _previous(results: List[dict], previous: List[dict]):
    reported: Dict[str, Tuple[str, Optional[str]]] = {}
    for r in results:
        if r.get("status") not in OK_STATUSES or not r.get("output"):
            continue
        for p in r["output"].get("previous_findings", []):
            cur = reported.get(p["id"])
            if cur is None or STATUS_RANK[p["status"]] < STATUS_RANK[cur[0]]:
                reported[p["id"]] = (p["status"], p.get("note"))
    out = []
    for prev in previous:
        e = dict(prev)
        if prev["id"] in reported:
            e["status"] = reported[prev["id"]][0]
            if reported[prev["id"]][1] is not None:
                e["note"] = reported[prev["id"]][1]
        else:
            e["status"] = _resolve(prev)
        out.append(e)
    return out


def _summaries(results: List[dict]) -> List[str]:
    out = []
    for r in sorted(results, key=lambda x: x["index"]):
        if r.get("status") not in OK_STATUSES or not r.get("output"):
            continue
        o = r["output"]
        line = "[c%d] %s" % (r["index"] + 1, o.get("summary", ""))
        if o.get("incomplete_reason"):
            line += " (미완료: %s)" % o["incomplete_reason"]
        out.append(line)
    return out


def merge(results, cfg, previous, changed, no_targets, skipped=None, anchor=None):
    kept, dropped = _collect(results, cfg, changed)
    findings = _dedupe(kept)
    for f in findings:
        in_block = f.get("severity") in cfg["block_severities"]
        if anchor is not None:
            f["anchor"] = anchor(f["file"], f["line"])
        f["id"] = finding_id(f)
        f["blocking"] = in_block and f["confidence"] >= cfg["block_confidence"]
        f["needs_check"] = in_block and not f["blocking"]
    prev_ids = {p["id"] for p in previous}
    # previous entries come from previous.relocate, so their "file" is already the renamed path
    prev_keys = {(p["file"], p["anchor"]) for p in previous if p.get("anchor")}
    findings = [f for f in findings if f["id"] not in prev_ids
                and not (f.get("anchor") and (f["file"], f["anchor"]) in prev_keys)]
    prev_out = _previous(results, previous)
    incomplete = sorted(r["index"] for r in results if r.get("status") != "ok")
    errors = [[r["index"], str(r.get("error") or r.get("status"))[:200]]
              for r in sorted(results, key=lambda x: x["index"]) if r.get("status") != "ok"]
    failed_all = bool(results) and all(r.get("status") == "failed" for r in results)
    skipped = list(skipped or [])
    unclear_blocking = any(p.get("blocking") and p["status"] == "unclear" for p in prev_out)
    blocking = any(f["blocking"] for f in findings) or any(
        p.get("blocking") and p["status"] == "open" for p in prev_out)
    if blocking:
        event = "REQUEST_CHANGES"
    elif no_targets or incomplete or failed_all or skipped or unclear_blocking:
        event = "COMMENT"
    else:
        event = "APPROVE"
    findings.sort(key=lambda f: (_sev(f), -f["confidence"], f["file"], f["line"]))
    return {"event": event, "findings": findings, "previous": prev_out,
            "incomplete": incomplete, "errors": errors, "failed_all": failed_all,
            "no_targets": no_targets, "summaries": _summaries(results),
            "dropped": dropped, "skipped": skipped, "unclear_blocking": unclear_blocking,
            "complete": not incomplete and not skipped and not failed_all}


def split_inline(findings, right_lines):
    inline, body = [], []
    for f in findings:
        (inline if f["line"] in right_lines.get(f["file"], ()) else body).append(f)
    return inline, body
