"""Parse unified diffs and pack them into prioritized, size-limited chunks."""
from __future__ import annotations

import fnmatch
import math
import os
import re
from typing import Dict, List, Set, Tuple

MARKER = "\n[truncated by ai-review]\n"
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
SRC_EXT = {".c", ".cc", ".cpp", ".cu", ".cuh", ".h", ".hpp", ".py", ".go", ".rs",
           ".java", ".js", ".ts", ".tsx", ".jsx", ".sh", ".cs", ".kt", ".swift"}
DOC_EXT = {".md", ".rst", ".txt", ".adoc"}
BUILD_EXT = {".yml", ".yaml", ".cmake", ".toml", ".gradle", ".mk"}
BUILD_NAMES = {"makefile", "cmakelists.txt", "dockerfile", "setup.py", "setup.cfg"}


def _unquote(s: str) -> str:
    s = s.strip()
    if not (len(s) >= 2 and s[0] == '"' and s[-1] == '"'):
        return s
    out, i, body = bytearray(), 0, s[1:-1]
    esc = {"n": 10, "t": 9, "r": 13, "a": 7, "b": 8, "f": 12, "v": 11}
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            m = re.match(r"[0-7]{1,3}", body[i + 1:i + 4])
            if m:
                out.append(int(m.group(0), 8) & 255)
                i += 1 + len(m.group(0))
                continue
            out.append(esc.get(nxt, ord(nxt) & 127))
            i += 2
            continue
        out.extend(ch.encode("utf-8"))
        i += 1
    return out.decode("utf-8", errors="replace")


def _strip_prefix(p: str) -> str:
    p = _unquote(p.split("\t")[0] if not p.startswith('"') else p)
    return p[2:] if p[:2] in ("a/", "b/") else p


def _git_line_paths(rest: str) -> Tuple[str, str]:
    if rest.startswith('"'):
        end = 1
        while rest[end] != '"' or rest[end - 1] == "\\":
            end += 1
        return _strip_prefix(rest[:end + 1]), _strip_prefix(rest[end + 1:].strip())
    if ' "b/' in rest:
        a, b = rest.split(' "b/', 1)
        return _strip_prefix(a), _strip_prefix('"b/' + b)
    half = (len(rest) - 1) // 2
    if rest[half] == " " and rest[:half][2:] == rest[half + 1:][2:]:
        return _strip_prefix(rest[:half]), _strip_prefix(rest[half + 1:])
    a, _, b = rest.partition(" b/")
    return _strip_prefix(a), _strip_prefix("b/" + b)


def _parse_one(text: str) -> dict:
    lines = text.split("\n")
    old, new = _git_line_paths(lines[0][len("diff --git "):])
    status, binary, added, right, nl = "M", False, [], [], 0
    in_hunk = False
    for ln in lines[1:]:
        if in_hunk and not ln.startswith("@@"):
            if ln.startswith("+"):
                added.append(nl); right.append(nl); nl += 1
            elif ln.startswith(" "):
                right.append(nl); nl += 1
            continue
        m = HUNK_RE.match(ln)
        if m:
            in_hunk, nl = True, int(m.group(1))
        elif ln.startswith("new file mode"):
            status = "A"
        elif ln.startswith("deleted file mode"):
            status = "D"
        elif ln.startswith("rename from "):
            status, old = "R", _unquote(ln[12:])
        elif ln.startswith("rename to "):
            status, new = "R", _unquote(ln[10:])
        elif ln.startswith("Binary files") or ln.startswith("GIT binary patch"):
            binary = True
    path = old if status == "D" else new
    return {"path": path, "old_path": old if status == "R" else None,
            "status": status, "binary": binary, "text": text,
            "added_lines": added, "right_lines": right}


def parse_unified_diff(text: str) -> List[dict]:
    parts = re.split(r"(?m)^(?=diff --git )", text)
    return [_parse_one(p) for p in parts if p.startswith("diff --git ")]


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 3.5)


def priority(path: str) -> int:
    low = path.lower()
    name, ext = os.path.basename(low), os.path.splitext(low)[1]
    parts = low.split("/")[:-1]
    if ext in DOC_EXT or "docs" in parts:
        return 3
    if (set(parts) & {"test", "tests"} or name.startswith("test_")
            or os.path.splitext(name)[0].endswith("_test")):
        return 2
    if ext in BUILD_EXT or name in BUILD_NAMES or ".github" in parts:
        return 1
    return 0 if ext in SRC_EXT else 4


def _split_hunks(text: str) -> Tuple[str, List[str]]:
    pieces = re.split(r"(?m)^(?=@@ )", text)
    return pieces[0], pieces[1:]


def _units(f: dict, limit: int) -> List[Tuple[str, str]]:
    text = f["text"] if f["text"].endswith("\n") else f["text"] + "\n"
    if estimate_tokens(text) <= limit:
        return [(f["path"], text)]
    header, hunks = _split_hunks(text)
    out = []
    for h in hunks or [""]:
        unit = header + h
        if estimate_tokens(unit) > limit:
            room = int(limit * 3.5) - len(header) - len(MARKER)
            cut = h[:max(room, 0)]
            if "\n" in cut:
                cut = cut[:cut.rindex("\n")]
            unit = header + cut + MARKER
        out.append((f["path"], unit))
    return out


def _excluded(path: str, globs: List[str]) -> bool:
    return any(fnmatch.fnmatch(path, g) or fnmatch.fnmatch("/" + path, g) for g in globs)


def build_chunks(files: List[dict], cfg: dict) -> Tuple[List[dict], List[str]]:
    limit = cfg["chunk_max_tokens"]
    targets = [f for f in files if not f["binary"] and f["status"] != "D"
               and not _excluded(f["path"], cfg["exclude_globs"])]
    targets.sort(key=lambda f: (priority(f["path"]), os.path.dirname(f["path"]),
                                os.path.splitext(os.path.basename(f["path"]))[0], f["path"]))
    packed: List[Tuple[List[str], str]] = []
    for f in targets:
        for path, unit in _units(f, limit):
            if packed and estimate_tokens(packed[-1][1] + unit) <= limit:
                if path not in packed[-1][0]:
                    packed[-1][0].append(path)
                packed[-1] = (packed[-1][0], packed[-1][1] + unit)
            else:
                packed.append(([path], unit))
    keep = packed[:cfg["max_chunks"]]
    skipped: List[str] = []
    for paths, _ in packed[cfg["max_chunks"]:]:
        skipped.extend(p for p in paths if p not in skipped)
    chunks = [{"index": i, "files": p, "diff": d, "est_tokens": estimate_tokens(d)}
              for i, (p, d) in enumerate(keep)]
    return chunks, skipped


def line_map(files: List[dict], key: str) -> Dict[str, Set[int]]:
    return {f["path"]: set(f[key]) for f in files}
