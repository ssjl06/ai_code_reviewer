"""Repository guideline files under .github/ai-review-rules/: read, write, and pick per chunk."""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Dict, List, Optional, Tuple

from review.diff_split import glob_match
from review.failure import log

RULES_DIR = ".github/ai-review-rules"
DIRS = ("general", "cpp", "cuda", "python", "shell", "yaml", "markdown", "paths")
MAX_LINES = 100
INDEX = "INDEX.md"
_PATH_RE = re.compile(r"^(%s)/[a-z0-9]+(-[a-z0-9]+)*\.md$" % "|".join(DIRS))
_HEAD_RE = re.compile(r"^## (R-[0-9a-f]{6})  (.+)$")
_TAIL_RE = re.compile(r"-\d+$")


def rule_id(path: str, rule: str) -> str:
    return "R-" + hashlib.sha1((path + "\n" + rule).encode("utf-8")).hexdigest()[:6]


def parse_doc(text: str) -> Optional[dict]:
    lines = text.split("\n")
    if len(lines) < 4 or lines[0] != "---":
        return None
    try:
        end = lines.index("---", 1)
    except ValueError:
        return None
    meta = {}
    for ln in lines[1:end]:
        k, sep, v = ln.partition(": ")
        if sep:
            meta[k] = v
    try:
        applies = json.loads(meta.get("applies", ""))
    except ValueError:
        return None
    if (not isinstance(applies, list) or not applies
            or not all(isinstance(g, str) and g for g in applies) or not meta.get("title")):
        return None
    entries: List[dict] = []
    for ln in lines[end + 1:]:
        m = _HEAD_RE.match(ln)
        if m:
            entries.append({"id": m.group(1), "text": m.group(2), "reason": None, "source": ""})
        elif entries and ln.startswith("- 이유: "):
            entries[-1]["reason"] = ln[len("- 이유: "):]
        elif entries and ln.startswith("- 출처: "):
            entries[-1]["source"] = ln[len("- 출처: "):]
    if not entries or any(not e["reason"] for e in entries):
        return None
    return {"applies": applies, "title": meta["title"], "rules": entries}


def render_doc(doc: dict) -> str:
    out = ["---", "applies: " + json.dumps(doc["applies"], ensure_ascii=False),
           "title: " + doc["title"], "---"]
    for r in doc["rules"]:
        out += ["## %s  %s" % (r["id"], r["text"]), "- 이유: " + r["reason"],
                "- 출처: " + r["source"]]
    return "\n".join(out) + "\n"


def check_path(path: str) -> Optional[str]:
    if _PATH_RE.match(path or ""):
        return None
    return ("지침 파일 경로는 <general|cpp|cuda|python|shell|yaml|markdown|paths>/<소문자-하이픈>.md "
            "형식이어야 한다: %s" % path)


def _abs(repo: str, rel: str = "") -> str:
    return os.path.join(repo, RULES_DIR, rel) if rel else os.path.join(repo, RULES_DIR)


def _linked(repo: str, rel: str) -> bool:
    """A symlink anywhere on the path from the repo root to the file."""
    cur = repo
    for part in (RULES_DIR + "/" + rel).split("/"):
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            return True
    return False


def _read(repo: str, rel: str) -> Optional[dict]:
    try:
        with open(_abs(repo, rel), encoding="utf-8") as f:
            return parse_doc(f.read())
    except (OSError, UnicodeDecodeError):
        return None


def _md_files(repo: str, d: str) -> List[str]:
    try:
        names = sorted(os.listdir(_abs(repo, d)))
    except OSError:
        return []
    return [d + "/" + n for n in names if n.endswith(".md") and n != INDEX]


def load_docs(repo: str) -> Dict[str, dict]:
    docs: Dict[str, dict] = {}
    for d in DIRS:
        for rel in _md_files(repo, d):
            doc = None if _linked(repo, rel) or not os.path.isfile(_abs(repo, rel)) \
                else _read(repo, rel)
            if doc is None:
                log("지침 파일 무시: %s" % (RULES_DIR + "/" + rel))
            else:
                docs[rel] = doc
    return docs


def select(docs: Dict[str, dict], files: List[str],
           max_chars: int) -> Tuple[List[Tuple[str, str]], List[str]]:
    """Guideline files whose `applies` match a chunk file: paths/ first, then by path, kept
    while the total stays within max_chars. -> ([(path, text)], omitted paths)."""
    hit = sorted((p for p, d in docs.items()
                  if any(glob_match(f, g) for f in files for g in d["applies"])),
                 key=lambda p: (not p.startswith("paths/"), p))
    chosen: List[Tuple[str, str]] = []
    omitted: List[str] = []
    used = 0
    for p in hit:
        text = render_doc(docs[p])
        if not omitted and used + len(text) <= max_chars:
            chosen.append((p, text))
            used += len(text)
        else:
            omitted.append(p)
    return chosen, omitted


def free_path(repo: str, path: str) -> str:
    """`path` when unused, else the next free `<name>-N.md` (N from 2) in its directory."""
    if not os.path.lexists(_abs(repo, path)):
        return path
    base = _TAIL_RE.sub("", path[:-3])
    n = 2
    while os.path.lexists(_abs(repo, "%s-%d.md" % (base, n))):
        n += 1
    return "%s-%d.md" % (base, n)


def _result(status: str, path: str, rid: str = "", reason: str = "") -> dict:
    return {"status": status, "path": path, "rule_id": rid, "reason": reason}


def _one_line(v) -> bool:
    return isinstance(v, str) and bool(v.strip()) and "\n" not in v and "\r" not in v


def _problem(change: dict, source: str, existing: Optional[dict]) -> Optional[str]:
    op, path = change.get("op"), change.get("path") or ""
    bad = check_path(path)
    if bad:
        return bad
    if op not in ("add", "update", "remove"):
        return "알 수 없는 지침 동작: %s" % op
    if op != "add" and not change.get("target"):
        return "수정·삭제할 규칙 id가 없다"
    if op != "remove":
        if not _one_line(change.get("rule")) or not _one_line(change.get("reason")):
            return "규칙과 이유는 한 줄이어야 한다"
        if not _one_line(source):
            return "출처는 한 줄이어야 한다"
    if op == "add" and existing is None:
        a = change.get("applies")
        if not (isinstance(a, list) and a and all(_one_line(g) for g in a)):
            return "새 지침 파일에는 applies 글롭 목록이 필요하다"
    if op != "add" and existing is None:
        return "지침 파일이 없다: %s" % path
    if op != "add" and change["target"] not in [r["id"] for r in existing["rules"]]:
        return "규칙 id가 파일에 없다: %s" % change["target"]
    return None


def apply_change(repo: str, change: dict, source: str) -> dict:
    path = change.get("path") or ""
    bad = check_path(path)
    if bad is None and _linked(repo, path):
        bad = "지침 경로에 심볼릭 링크가 있다: %s" % path
    existing = None
    if bad is None and os.path.exists(_abs(repo, path)):
        existing = _read(repo, path)
        if existing is None:
            bad = "지침 파일을 읽을 수 없다: %s" % path
    bad = bad or _problem(change, source, existing)
    if bad:
        return _result("invalid", path, reason=bad)
    op = change["op"]
    doc = existing or {"applies": change.get("applies"), "title": os.path.basename(path)[:-3],
                       "rules": []}
    rules_ = doc["rules"]
    if op == "remove":
        rid = change["target"]
        doc["rules"] = [r for r in rules_ if r["id"] != rid]
    else:
        rid = rule_id(path, change["rule"])
        entry = {"id": rid, "text": change["rule"], "reason": change["reason"], "source": source}
        if op == "add":
            if any(r["id"] == rid for r in rules_):
                return _result("unchanged", path, rid)
            rules_.append(entry)
        else:
            i = next(n for n, r in enumerate(rules_) if r["id"] == change["target"])
            if rules_[i] == entry:
                return _result("unchanged", path, rid)
            if rid != change["target"] and any(r["id"] == rid for r in rules_):
                return _result("invalid", path, reason="같은 규칙이 이미 있다")
            rules_[i] = entry
    if not doc["rules"]:
        os.remove(_abs(repo, path))
        return _result("removed", path, rid)
    text = render_doc(doc)
    if text.count("\n") > MAX_LINES:
        return _result("overflow", path, rid)
    os.makedirs(os.path.dirname(_abs(repo, path)), exist_ok=True)
    with open(_abs(repo, path), "w", encoding="utf-8") as f:
        f.write(text)
    return _result({"add": "added", "update": "updated", "remove": "removed"}[op], path, rid)


def _write_index(repo: str, rel: str, lines: List[str], written: List[str]) -> None:
    if not lines:
        if os.path.exists(_abs(repo, rel)):
            os.remove(_abs(repo, rel))
        return
    text = "\n".join(lines) + "\n"
    with open(_abs(repo, rel), "w", encoding="utf-8") as f:
        f.write(text)
    written.append(RULES_DIR + "/" + rel)
    if len(lines) > MAX_LINES:
        log("지침 색인이 %d줄을 넘는다: %s" % (MAX_LINES, RULES_DIR + "/" + rel))


def write_indexes(repo: str) -> List[str]:
    """Regenerate INDEX.md files; -> the repo-relative paths written (empty ones are removed)."""
    docs = load_docs(repo)
    written: List[str] = []
    top: List[str] = []
    for d in DIRS:
        mine = sorted(p for p in docs if p.startswith(d + "/"))
        if mine:
            top.append("- %s/: 파일 %d개" % (d, len(mine)))
            _write_index(repo, d + "/" + INDEX, ["- %s: %s" % (p, docs[p]["title"]) for p in mine],
                         written)
        elif os.path.isdir(_abs(repo, d)):
            _write_index(repo, d + "/" + INDEX, [], written)
    if top or os.path.isdir(_abs(repo)):
        _write_index(repo, INDEX, top, written)
    return written
