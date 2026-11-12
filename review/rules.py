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
    if isinstance(path, str) and _PATH_RE.fullmatch(path):
        return None
    return ("지침 파일 경로는 <general|cpp|cuda|python|shell|yaml|markdown|paths>/<소문자-하이픈>.md "
            "형식이어야 한다: %s" % path)


def abs_path(repo: str, rel: str = "") -> str:
    return os.path.join(repo, RULES_DIR, rel) if rel else os.path.join(repo, RULES_DIR)


def linked(repo: str, rel: str) -> bool:
    """A symlink anywhere on the path from the repo root to the file."""
    cur = repo
    for part in (RULES_DIR + "/" + rel).split("/"):
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            return True
    return False


def read_doc(repo: str, rel: str) -> Optional[dict]:
    try:
        with open(abs_path(repo, rel), encoding="utf-8") as f:
            return parse_doc(f.read())
    except (OSError, UnicodeDecodeError):
        return None


def md_files(repo: str, d: str) -> List[str]:
    try:
        names = sorted(os.listdir(abs_path(repo, d)))
    except OSError:
        return []
    return [d + "/" + n for n in names if n.endswith(".md") and n != INDEX]


def load_docs(repo: str) -> Dict[str, dict]:
    docs: Dict[str, dict] = {}
    for d in DIRS:
        for rel in md_files(repo, d):
            doc = None
            if check_path(rel) is None and not linked(repo, rel) \
                    and os.path.isfile(abs_path(repo, rel)):
                doc = read_doc(repo, rel)
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
