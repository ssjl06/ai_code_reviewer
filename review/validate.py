"""Normalize untrusted chunk output and previous bot state to the shapes the kit relies on."""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from review.ledger import CLOSED, from_v2

SEVERITIES = ("critical", "major", "minor", "info")
CATEGORIES = ("bug", "security", "concurrency", "memory", "performance", "api-misuse",
              "maintainability", "docs")
STATUSES = ("resolved", "open", "unclear")
TITLE_MAX = 80
ISSUE_STATUSES = ("open",) + CLOSED  # the ledger's; `unclear` is a per-run outcome only
# `major` and `minor` share their first letter, so minor is `n` in the compact form
_SEV_CODE = {"critical": "c", "major": "m", "minor": "n", "info": "i"}
_SEV_BY_CODE = {v: k for k, v in _SEV_CODE.items()}
_CLOSED_CODE = {"r": "resolved", "w": "waived"}


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
    if isinstance(raw.get("same_as"), str):
        f["same_as"] = raw["same_as"]
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
    reviewed = obj.get("diff_reviewed")
    out = {"summary": _str(obj.get("summary")),
            "findings": [f for f in map(_finding, obj["findings"]) if f is not None],
            "previous_findings": [p for p in map(_previous, prev if isinstance(prev, list) else [])
                                  if p is not None],
            "incomplete_reason": reason if isinstance(reason, str) else None,
            "diff_reviewed": reviewed is True}  # only an explicit boolean true counts
    if not isinstance(reviewed, bool):
        out["diff_reviewed_invalid"] = True  # missing / null / not a boolean, not the model's false
    return out


def _state_entry(e: Any) -> bool:
    return (isinstance(e, dict) and isinstance(e.get("id"), str)
            and isinstance(e.get("file"), str) and isinstance(e.get("line"), int)
            and not isinstance(e.get("line"), bool) and isinstance(e.get("title"), str)
            and isinstance(e.get("severity"), str) and isinstance(e.get("blocking"), bool))


def _str_map(v: Any) -> dict:
    return {k: x for k, x in v.items() if isinstance(k, str) and isinstance(x, str)} \
        if isinstance(v, dict) else {}


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _text(e: dict, key: str) -> str:
    return e[key] if isinstance(e.get(key), str) else ""


def _issue(e: Any) -> Optional[dict]:
    """A v3 issue record, or None when an identity field is missing or mistyped. The display
    fields (title, excerpt) and anchor may be absent: `state_fit` blanks them."""
    if not (isinstance(e, dict) and isinstance(e.get("id"), str) and isinstance(e.get("file"), str)
            and _is_int(e.get("line")) and e.get("status") in ISSUE_STATUSES
            and isinstance(e.get("severity"), str) and isinstance(e.get("blocking"), bool)):
        return None
    line = e["line"]
    lo, hi = e.get("lo"), e.get("hi")
    if not (_is_int(lo) and _is_int(hi) and lo <= hi):
        lo = hi = line
    out = {"id": e["id"], "file": e["file"], "line": line, "lo": lo, "hi": hi,
           "status": e["status"], "severity": e["severity"], "blocking": e["blocking"],
           "anchor": _text(e, "anchor"), "title": _text(e, "title"),
           "excerpt": _text(e, "excerpt"), "orphan": e.get("orphan") is True}
    if isinstance(e.get("via"), str) and e["via"]:
        out["via"] = e["via"]
    return out


def compact_issue(i: dict) -> str:
    """`<id>:<line>:<lo>:<hi>:<s><v>` of a closed issue, then `:<via>` when it has one; an
    orphan has lo and hi 0."""
    orphan = bool(i.get("orphan"))
    return "%s:%d:%d:%d:%s%s%s" % (
        i["id"], i["line"], 0 if orphan else max(1, i["lo"]), 0 if orphan else max(1, i["hi"]),
        "r" if i["status"] == "resolved" else "w", _SEV_CODE.get(i["severity"], "n"),
        ":" + i["via"] if i.get("via") else "")


def expand_issue(path: str, token: str) -> Optional[dict]:
    """Inverse of `compact_issue` (5 fields, or 6 with `via`); display fields come back empty
    and `blocking` False."""
    parts = token.split(":")
    if len(parts) not in (5, 6) or len(parts[4]) != 2 or not parts[0] or not parts[-1]:
        return None
    status, sev = _CLOSED_CODE.get(parts[4][0]), _SEV_BY_CODE.get(parts[4][1])
    try:
        line, lo, hi = int(parts[1]), int(parts[2]), int(parts[3])
    except ValueError:
        return None
    if status is None or sev is None or line < 1 or lo < 0 or hi < lo:
        return None
    orphan = lo == 0 and hi == 0
    return {"id": parts[0], "file": path, "line": line, "lo": line if orphan else lo,
            "hi": line if orphan else hi, "status": status, "severity": sev, "blocking": False,
            "anchor": "", "title": "", "excerpt": "", "orphan": orphan,
            **({"via": parts[5]} if len(parts) == 6 else {})}


def _expand_closed(closed: Any) -> list:
    out = []
    for path, text in _str_map(closed).items():
        out += [i for i in (expand_issue(path, t) for t in text.split()) if i is not None]
    return out


def _str_list(v: Any) -> Optional[list]:
    return list(v) if isinstance(v, list) and all(isinstance(x, str) for x in v) else None


def normalize_mention(m: Any) -> Optional[dict]:
    """The receipt `m` of a mention review (`k`, `w`, `n`, `up`, optional `g`), or None when
    malformed."""
    if not isinstance(m, dict) or not isinstance(m.get("k"), str) \
            or not re.match(r"[irv]:\d+\Z", m["k"]):
        return None
    lists = {f: _str_list(m.get(f)) for f in ("w", "n", "up")}
    if any(v is None for v in lists.values()) or ("g" in m and not isinstance(m["g"], str)):
        return None
    out = dict(k=m["k"], **lists)
    if "g" in m:
        out["g"] = m["g"]
    return out


def normalize_state(state: Any) -> Optional[dict]:
    """Previous bot state as v3 with malformed parts dropped: a v2 marker is migrated (each
    finding becomes an open issue), the compact `closed` map of a v3 marker is expanded into
    `issues`. None for anything else (a v1 state means "no previous state": a full review)."""
    if not isinstance(state, dict) or state.get("v") not in (2, 3):
        return None
    sha = state.get("sha")
    out = {"v": 3, "sha": sha if isinstance(sha, str) else "",
           "complete": state.get("complete") is True,
           "seen": _str_map(state.get("seen")), "seen_files": _str_map(state.get("seen_files")),
           "seen_hunks": _str_map(state.get("seen_hunks")),
           "approvable": state.get("approvable") is True,
           "unclear": ([x for x in state["unclear"] if isinstance(x, str)]
                       if isinstance(state.get("unclear"), list) else None)}  # None: an old marker
    m = normalize_mention(state.get("m"))
    if m is not None:
        out["m"] = m
    if state["v"] == 2:
        found = state.get("findings")
        issues = [from_v2(dict(e, anchor=e.get("anchor") if isinstance(e.get("anchor"), str)
                               else ""))
                  for e in (found if isinstance(found, list) else []) if _state_entry(e)]
        pending = []
    else:
        raw = state.get("issues")
        issues = [i for i in map(_issue, raw if isinstance(raw, list) else []) if i is not None]
        issues += _expand_closed(state.get("closed"))
        rp = state.get("recheck_pending")
        pending = [x for x in rp if isinstance(x, str)] if isinstance(rp, list) else []
        if _is_int(state.get("dropped_closed")) and state["dropped_closed"] > 0:
            out["dropped_closed"] = state["dropped_closed"]
        cut = _str_map(state.get("cut"))
        if cut:  # prints of lines too long for a chunk (`scope.next_state`)
            out["cut"] = cut
    by_id = {}
    for i in issues:  # the first record of an id wins
        by_id.setdefault(i["id"], i)
    out["issues"] = list(by_id.values())
    out["recheck_pending"] = pending
    return out


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
