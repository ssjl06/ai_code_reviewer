"""Thin git subprocess helpers."""
from __future__ import annotations

import re
import subprocess
from typing import Dict, List


class GitError(RuntimeError):
    """Raised when a git command exits non-zero."""


def git(repo: str, *args: str, timeout: int = 60) -> str:
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotepath=false", *args],
            cwd=repo, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise GitError("git %s timed out after %ss" % (args[0] if args else "", e.timeout))
    if proc.returncode != 0:
        raise GitError(proc.stderr)
    return proc.stdout


def is_ancestor(repo: str, ancestor: str, head: str) -> bool:
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, head],
        cwd=repo, capture_output=True, text=True, timeout=60)
    return proc.returncode == 0


def diff_text(repo: str, base: str, head: str, merge_base: bool) -> str:
    sep = "..." if merge_base else ".."
    return git(repo, "diff", "--no-color", "--no-ext-diff", "-M", "-U3",
               base + sep + head, timeout=120)


def read_blobs(repo: str, rev: str, paths: List[str]) -> Dict[str, List[str]]:
    """Read file contents at `rev` via one `git cat-file --batch`; missing paths are omitted."""
    paths = [p for p in paths if "\n" not in p]
    if not paths:
        return {}
    stdin = "".join("%s:%s\n" % (rev, p) for p in paths).encode("utf-8")
    try:
        proc = subprocess.run(["git", "-c", "core.quotepath=false", "cat-file", "--batch"],
                              cwd=repo, input=stdin, capture_output=True, timeout=120)
    except subprocess.TimeoutExpired as e:
        raise GitError("git cat-file timed out after %ss" % e.timeout)
    if proc.returncode != 0:
        raise GitError(proc.stderr.decode("utf-8", errors="replace"))
    out, pos, blobs = proc.stdout, 0, {}
    for p in paths:
        end = out.index(b"\n", pos)
        head = out[pos:end].decode("utf-8", errors="replace").rsplit(" ", 2)
        pos = end + 1
        if len(head) != 3 or not head[2].isdigit():
            continue  # "<name> missing" (no content follows)
        size = int(head[2])
        body = out[pos:pos + size]
        pos += size + 1  # content and its trailing newline, whatever the type
        if head[1] != "blob":
            continue
        lines = body.decode("utf-8", errors="replace").split("\n")
        blobs[p] = lines[:-1] if lines[-1] == "" else lines
    return blobs


def renames_between(repo: str, old_rev: str, new_rev: str) -> Dict[str, str]:
    """Renames old path -> new path from `old_rev` to `new_rev`; {} when `old_rev` is not a
    commit sha available locally (e.g. a force-pushed head that was never fetched)."""
    if not re.fullmatch(r"[0-9a-f]{7,64}", old_rev or ""):
        return {}
    ok = subprocess.run(["git", "cat-file", "-e", old_rev + "^{commit}"], cwd=repo,
                        capture_output=True, timeout=60)
    if ok.returncode != 0:
        return {}
    parts = git(repo, "diff", "--name-status", "-M", "-z", old_rev, new_rev,
                timeout=120).split("\0")
    out, i = {}, 0
    while i < len(parts) and parts[i]:
        status = parts[i]
        n = 2 if status[:1] in ("R", "C") else 1
        if status[:1] == "R" and i + 2 < len(parts):
            out[parts[i + 1]] = parts[i + 2]
        i += 1 + n
    return out
