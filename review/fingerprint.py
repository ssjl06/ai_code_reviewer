"""Content fingerprints for lines and the seen-set codec (pure functions)."""
from __future__ import annotations

import base64
import binascii
import hashlib
from collections import Counter
from typing import Dict, Iterable, List, Optional, Set, Tuple

SEEN_CAP = 24000


def norm(s: str) -> str:
    return s.rstrip()


def _sha4(s: str) -> bytes:
    return hashlib.sha1(s.encode("utf-8")).digest()[:4]


def _at(lines: List[str], n: int) -> str:
    """Normalized 1-based line, "" when out of range."""
    return norm(lines[n - 1]) if 1 <= n <= len(lines) else ""


def fp_line(lines: List[str], n: int) -> bytes:
    return _sha4(_at(lines, n - 1) + "\n" + _at(lines, n) + "\n" + _at(lines, n + 1))


def fp_removed(lines: List[str], pos: int, text: str) -> bytes:
    return _sha4("-\n" + _at(lines, pos - 1) + "\n" + norm(text) + "\n" + _at(lines, pos))


def anchor_line(lines: List[str], pos: int) -> int:
    return pos if pos <= len(lines) else max(len(lines), 1)


def hunk_key(old_start: int, new_start: int, prints: Iterable[bytes]) -> bytes:
    """Position + content of one (sub-)hunk: both header starts, since the sub-hunks of a
    deletion share their new-side start (and, for repeated text, their prints)."""
    head = b"%d,%d\n" % (old_start, new_start)
    return hashlib.sha1(head + b"".join(sorted(prints))).digest()[:4]


def pack(c: Counter) -> str:
    return base64.b64encode(b"".join(sorted(c.elements()))).decode("ascii")


def unpack(s) -> Counter:
    if not isinstance(s, str):
        return Counter()
    try:
        raw = base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError):
        return Counter()
    if len(raw) % 4:
        return Counter()
    return Counter(raw[i:i + 4] for i in range(0, len(raw), 4))


def whole_hash(c: Counter) -> bytes:
    return hashlib.sha1(b"".join(sorted(c.elements()))).digest()[:4]


def encode_seen(seen: Dict[str, Counter], cap: int = SEEN_CAP,
                full: Optional[Set[str]] = None) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Over `cap`, files become whole-file hashes: fully reviewed files (`full`) first, since
    a partial file's hash never matches its head; largest first within each group."""
    raw = {p: pack(c) for p, c in seen.items() if sum(c.values()) > 0}
    files: Dict[str, str] = {}
    full = full or set()
    while raw and sum(len(v) for v in raw.values()) > cap:
        path = max(raw, key=lambda p: (p in full, len(raw[p]), p))
        files[path] = base64.b64encode(whole_hash(seen[path])).decode("ascii")
        del raw[path]
    return raw, files


def decode_hunks(state: Optional[dict]) -> Dict[str, Counter]:
    raw = state.get("seen_hunks") if isinstance(state, dict) else None
    out = {p: unpack(v) for p, v in raw.items()} if isinstance(raw, dict) else {}
    return {p: c for p, c in out.items() if isinstance(p, str) and c}


def decode_seen(state: Optional[dict]) -> Tuple[Dict[str, Counter], Dict[str, bytes]]:
    seen: Dict[str, Counter] = {}
    files: Dict[str, bytes] = {}
    if not isinstance(state, dict):
        return seen, files
    raw = state.get("seen")
    if isinstance(raw, dict):
        for p, v in raw.items():
            c = unpack(v)
            if isinstance(p, str) and c:
                seen[p] = c
    raw = state.get("seen_files")
    if isinstance(raw, dict):
        for p, v in raw.items():
            if not (isinstance(p, str) and isinstance(v, str)):
                continue
            try:
                h = base64.b64decode(v, validate=True)
            except (binascii.Error, ValueError):
                continue
            if len(h) == 4:
                files[p] = h
    return seen, files
