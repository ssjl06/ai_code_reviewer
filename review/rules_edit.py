"""Applying a guideline change to the files under .github/ai-review-rules/, and the indexes."""
from __future__ import annotations

import os
import re
from typing import List, Optional

from review.failure import log
from review.rules import (DIRS, INDEX, MAX_LINES, RULES_DIR, abs_path, check_path,
                          linked, load_docs, read_doc, render_doc, rule_id)

_TAIL_RE = re.compile(r"-\d+$")


def free_path(repo: str, path: str) -> str:
    """`path` when unused, else the next free `<name>-N.md` (N from 2) in its directory."""
    if not os.path.lexists(abs_path(repo, path)):
        return path
    base = _TAIL_RE.sub("", path[:-3])
    n = 2
    while os.path.lexists(abs_path(repo, "%s-%d.md" % (base, n))):
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
    if bad is None and linked(repo, path):
        bad = "지침 경로에 심볼릭 링크가 있다: %s" % path
    existing = None
    if bad is None and os.path.exists(abs_path(repo, path)):
        existing = read_doc(repo, path)
        if existing is None:
            bad = "지침 파일을 읽을 수 없다: %s" % path
    bad = bad or _problem(change, source, existing)
    if bad:
        return _result("invalid", path, reason=bad)
    op = change["op"]
    doc = existing or {"applies": change.get("applies"), "title": os.path.basename(path)[:-3],
                       "rules": []}
    rules_ = doc["rules"]
    old_lines = render_doc(doc).count("\n") if existing else 0
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
        os.remove(abs_path(repo, path))
        return _result("removed", path, rid)
    text = render_doc(doc)
    # a file already over the limit may still shrink or stay as long
    if text.count("\n") > MAX_LINES and text.count("\n") > old_lines:
        return _result("overflow", path, rid)
    os.makedirs(os.path.dirname(abs_path(repo, path)), exist_ok=True)
    with open(abs_path(repo, path), "w", encoding="utf-8") as f:
        f.write(text)
    return _result({"add": "added", "update": "updated", "remove": "removed"}[op], path, rid)


def _write_index(repo: str, rel: str, lines: List[str], written: List[str]) -> None:
    if linked(repo, rel):
        log("지침 색인 무시: %s" % (RULES_DIR + "/" + rel))
        return
    if not lines:
        if os.path.exists(abs_path(repo, rel)):
            os.remove(abs_path(repo, rel))
        return
    text = "\n".join(lines) + "\n"
    with open(abs_path(repo, rel), "w", encoding="utf-8") as f:
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
        elif os.path.isdir(abs_path(repo, d)):
            _write_index(repo, d + "/" + INDEX, [], written)
    if top or os.path.isdir(abs_path(repo)):
        _write_index(repo, INDEX, top, written)
    return written
