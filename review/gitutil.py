"""Thin git subprocess helpers."""
from __future__ import annotations

import subprocess


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
