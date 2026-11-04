"""Detect non-Korean review prose and merge a Korean re-ask into the original chunk output."""
from __future__ import annotations

import re
from typing import Iterator, Optional

KOREAN_MIN_RATIO = 0.3   # Hangul share of the letters required to count as Korean
KOREAN_MIN_LETTERS = 12  # fewer letters than this (e.g. "LGTM") is too short to judge
_CODE = re.compile(r"`+[^`]*`+")
_HANGUL = re.compile(r"[ᄀ-ᇿ㄰-㆏가-힣]")
_ASCII = re.compile(r"[A-Za-z]")


def _prose(output: dict) -> Iterator[str]:
    """Each prose item to judge; `suggestion` is left out because it is often code."""
    yield output.get("summary") or ""
    yield output.get("incomplete_reason") or ""
    for f in output.get("findings") or []:
        yield f.get("title") or ""
        yield f.get("detail") or ""
    for p in output.get("previous_findings") or []:
        yield p.get("note") or ""


def _item_korean(text: str) -> bool:
    text = _CODE.sub(" ", text)
    hangul, ascii_ = len(_HANGUL.findall(text)), len(_ASCII.findall(text))
    letters = hangul + ascii_
    return letters < KOREAN_MIN_LETTERS or hangul >= KOREAN_MIN_RATIO * letters


def is_korean(output: dict) -> bool:
    """True when every prose item, outside inline code, is Korean or too short to judge."""
    return all(_item_korean(t) for t in _prose(output))


def _key(f: dict) -> tuple:
    return f["file"], f["line"], f["severity"], f["category"]


def merge_korean(orig: dict, new: Optional[dict]) -> Optional[dict]:
    """`orig` with the prose of `new`, or None when `new` is unusable or changed the findings."""
    if not new or [_key(f) for f in new["findings"]] != [_key(f) for f in orig["findings"]]:
        return None
    if ([(p["id"], p["status"]) for p in new["previous_findings"]]
            != [(p["id"], p["status"]) for p in orig["previous_findings"]]):
        return None
    out = dict(orig, summary=new["summary"] or orig["summary"])
    if orig.get("incomplete_reason") and new.get("incomplete_reason"):
        out["incomplete_reason"] = new["incomplete_reason"]
    out["findings"] = []
    for f, g in zip(orig["findings"], new["findings"]):
        m = dict(f, title=g["title"] or f["title"], detail=g["detail"] or f["detail"])
        if g.get("suggestion") and f.get("suggestion"):
            m["suggestion"] = g["suggestion"]
        out["findings"].append(m)
    out["previous_findings"] = [dict(p, note=q["note"]) if q.get("note") and p.get("note")
                                else dict(p)
                                for p, q in zip(orig["previous_findings"], new["previous_findings"])]
    return out if is_korean(out) else None
