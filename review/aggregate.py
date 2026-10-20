"""Merge chunk findings and compute the deterministic review verdict."""
from __future__ import annotations

import hashlib
import re
from typing import Dict, List, Optional, Set, Tuple

SEV_RANK = {"critical": 0, "major": 1, "minor": 2, "info": 3}
OK_STATUSES = ("ok", "partial")
STATUS_RANK = {"open": 0, "unclear": 1, "resolved": 2}
_ROOT = re.compile(r"^(?:\./|/)+")
_DIFF_PREFIX = re.compile(r"^[ab]/")


def finding_id(f: dict) -> str:
    key = "%s:%s:%s" % (f.get("file"), f.get("anchor") or f.get("line"), f.get("title"))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]


def normalize_path(p: str, known: Optional[Set[str]] = None) -> str:
    """Strip `./` and `/`; strip one `a/`/`b/` diff prefix unless the path is a known file."""
    raw = _ROOT.sub("", p)
    return raw if known and raw in known else _DIFF_PREFIX.sub("", raw)


def _sev(f: dict) -> int:
    return SEV_RANK.get(f.get("severity"), len(SEV_RANK))


def _strength(f: dict) -> Tuple[int, int]:
    return (_sev(f), -f.get("confidence", 0))


def _collect(results: List[dict], cfg: dict, changed: Optional[Dict[str, Set[int]]],
             chunk_files=None):
    known = set(changed) if changed is not None else (
        {f for fs in chunk_files.values() for f in fs} if chunk_files else None)
    kept, dropped = [], 0
    for r in results:
        if r.get("status") not in OK_STATUSES or not r.get("output"):
            continue
        for raw in r["output"].get("findings", []):
            f = dict(raw)
            f["file"] = normalize_path(f["file"], known)
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


def _previous(results: List[dict], previous: List[dict], chunk_files=None):
    """Model reports win over `_resolve`; "resolved" counts only from an ok chunk that holds
    the file and only when every chunk holding that file is ok (None: chunks hold all files)."""
    status = {r["index"]: r.get("status") for r in results}
    holds = lambda i, path: chunk_files is None or path in chunk_files.get(i, ())
    files = {p["id"]: p["file"] for p in previous}
    reported: Dict[str, Tuple[str, Optional[str]]] = {}
    for r in results:
        if r.get("status") not in OK_STATUSES or not r.get("output"):
            continue
        for p in r["output"].get("previous_findings", []):
            path = files.get(p["id"], "")
            if p["status"] == "resolved" and not (r["status"] == "ok" and holds(r["index"], path)
                                                  and all(st == "ok" for i, st in status.items()
                                                          if holds(i, path))):
                continue
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


def merge(results, cfg, previous, changed, no_targets, skipped=None, anchor=None,
          chunk_files: Optional[Dict[int, List[str]]] = None, truncated=None):
    kept, dropped = _collect(results, cfg, changed, chunk_files)
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
    prev_keys = {(p["file"], p["anchor"]): p.get("blocking") for p in previous if p.get("anchor")}
    findings = [f for f in findings if f["id"] not in prev_ids and not (
        (f["file"], f.get("anchor")) in prev_keys
        and not (f["blocking"] and not prev_keys[(f["file"], f["anchor"])]))]
    prev_out = _previous(results, previous, chunk_files)
    incomplete = sorted(r["index"] for r in results if r.get("status") != "ok")
    errors = [[r["index"], str(r.get("error") or r.get("status"))[:200]]
              for r in sorted(results, key=lambda x: x["index"]) if r.get("status") != "ok"]
    failed_all = bool(results) and all(r.get("status") == "failed" for r in results)
    skipped, truncated = list(skipped or []), list(truncated or [])
    unclear_blocking = any(p.get("blocking") and p["status"] == "unclear" for p in prev_out)
    blocking = any(f["blocking"] for f in findings) or any(
        p.get("blocking") and p["status"] == "open" for p in prev_out)
    if blocking:
        event = "REQUEST_CHANGES"
    elif no_targets or incomplete or failed_all or skipped or truncated or unclear_blocking:
        event = "COMMENT"
    else:
        event = "APPROVE"
    findings.sort(key=lambda f: (_sev(f), -f["confidence"], f["file"], f["line"]))
    return {"event": event, "findings": findings, "previous": prev_out,
            "incomplete": incomplete, "errors": errors, "failed_all": failed_all,
            "no_targets": no_targets, "summaries": _summaries(results),
            "dropped": dropped, "skipped": skipped, "unclear_blocking": unclear_blocking,
            "truncated": truncated,
            "complete": not (incomplete or skipped or truncated or failed_all)}


def split_inline(findings, right_lines):
    inline, body = [], []
    for f in findings:
        (inline if f["line"] in right_lines.get(f["file"], ()) else body).append(f)
    return inline, body
