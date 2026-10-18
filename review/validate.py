"""Normalize untrusted chunk output and previous bot state to the shapes the kit relies on."""
from __future__ import annotations

import json
from typing import Any, Optional

SEVERITIES = ("critical", "major", "minor", "info")
CATEGORIES = ("bug", "security", "concurrency", "memory", "performance", "api-misuse",
              "maintainability", "docs")
STATUSES = ("resolved", "open", "unclear")
TITLE_MAX = 80


def _int(v: Any) -> Optional[int]:
    """int, or an int-like str; bools and everything else -> None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        try:
            return int(v.strip())
        except ValueError:
            return None
    return None


def _str(v: Any) -> str:
    return v if isinstance(v, str) else ("" if v is None else str(v))


def _finding(raw: Any) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    path, line, conf = raw.get("file"), _int(raw.get("line")), _int(raw.get("confidence"))
    if not isinstance(path, str) or not path or line is None or line < 1 or conf is None:
        return None
    if raw.get("severity") not in SEVERITIES:
        return None
    cat = raw.get("category")
    f = {"file": path, "line": line, "severity": raw["severity"],
         "confidence": max(0, min(100, conf)),
         "category": cat if cat in CATEGORIES else "bug",
         "title": _str(raw.get("title"))[:TITLE_MAX], "detail": _str(raw.get("detail"))}
    if isinstance(raw.get("suggestion"), str):
        f["suggestion"] = raw["suggestion"]
    end = _int(raw.get("end_line"))
    if end is not None and end >= line:
        f["end_line"] = end
    return f


def _previous(raw: Any) -> Optional[dict]:
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
        return None
    if raw.get("status") not in STATUSES:
        return None
    p = {"id": raw["id"], "status": raw["status"]}
    if isinstance(raw.get("note"), str):
        p["note"] = raw["note"]
    return p


def normalize_output(obj: Any) -> Optional[dict]:
    """Schema-shaped chunk output, or None when the top level is unusable."""
    if not isinstance(obj, dict) or not isinstance(obj.get("findings"), list):
        return None
    prev = obj.get("previous_findings")
    reason = obj.get("incomplete_reason")
    return {"summary": _str(obj.get("summary")),
            "findings": [f for f in map(_finding, obj["findings"]) if f is not None],
            "previous_findings": [p for p in map(_previous, prev if isinstance(prev, list) else [])
                                  if p is not None],
            "incomplete_reason": reason if isinstance(reason, str) else None}


def _state_entry(e: Any) -> bool:
    return (isinstance(e, dict) and isinstance(e.get("id"), str)
            and isinstance(e.get("file"), str) and isinstance(e.get("line"), int)
            and not isinstance(e.get("line"), bool) and isinstance(e.get("title"), str)
            and isinstance(e.get("severity"), str) and isinstance(e.get("blocking"), bool))


def normalize_state(state: Any) -> Optional[dict]:
    """Previous bot State with malformed entries dropped; None when not a dict."""
    if not isinstance(state, dict):
        return None
    sha, findings = state.get("sha"), state.get("findings")
    return {"sha": sha if isinstance(sha, str) else "",
            "findings": [dict(e) for e in (findings if isinstance(findings, list) else [])
                         if _state_entry(e)]}


def extract_output(result: Optional[dict]) -> Optional[dict]:
    """Normalized chunk output from the final `result` event (structured_output or text JSON)."""
    if not isinstance(result, dict):
        return None
    if isinstance(result.get("structured_output"), dict):
        return normalize_output(result["structured_output"])
    text, dec, i = result.get("result"), json.JSONDecoder(), 0
    if not isinstance(text, str):
        return None
    while True:
        i = text.find("{", i)
        if i < 0:
            return None
        try:
            obj, end = dec.raw_decode(text, i)
        except ValueError:
            i += 1
            continue
        out = normalize_output(obj) if isinstance(obj, dict) and "findings" in obj else None
        if out is not None:
            return out
        i = end if isinstance(obj, dict) else i + 1
