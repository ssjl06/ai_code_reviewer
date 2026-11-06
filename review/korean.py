"""Detect review output with no Korean at all and merge a Korean re-ask into the original chunk output."""
from __future__ import annotations

import re
from typing import Iterator, Optional

KOREAN_MIN_LETTERS = 12  # fewer ASCII letters than this in the whole output (e.g. "LGTM") is too short to judge
_CODE = re.compile(r"`+[^`]*`+")
_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,}).*?(?:^[ \t]*\1[`~]*[ \t]*$|\Z)", re.M | re.S)
_HANGUL = re.compile(r"[ᄀ-ᇿ㄰-㆏가-힣]")
_ASCII = re.compile(r"[A-Za-z]")


def _strip_code(text: str) -> str:
    """`text` without fenced blocks and inline code. Per item: an unclosed fence runs to its end."""
    return _CODE.sub(" ", _FENCE.sub("", text))


def _prose(output: dict) -> Iterator[str]:
    """Each prose item to judge."""
    yield output.get("summary") or ""
    yield output.get("incomplete_reason") or ""
    for f in output.get("findings") or []:
        yield f.get("title") or ""
        yield f.get("detail") or ""
        yield f.get("suggestion") or ""
    for p in output.get("previous_findings") or []:
        yield p.get("note") or ""


def is_korean(output: dict) -> bool:
    """False only when the prose, outside code, has no Hangul at all and enough English to judge.

    One Hangul character anywhere counts as Korean; mixed output is not re-asked. Output with
    fewer than KOREAN_MIN_LETTERS ASCII letters in total ("LGTM", empty) is too short to judge."""
    letters = 0
    for item in _prose(output):
        text = _strip_code(item)
        if _HANGUL.search(text):
            return True
        letters += len(_ASCII.findall(text))
    return letters < KOREAN_MIN_LETTERS


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
