"""Small CLI helpers: overrides, dry-run state file, step summary, output dir."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from review.render import render_step_summary
from review.validate import normalize_state


def parse_overrides(items: Optional[List[str]]) -> Dict[str, Any]:
    """`key=value` pairs; value is JSON, falling back to the raw string."""
    out: Dict[str, Any] = {}
    for item in items or []:
        key, sep, raw = item.partition("=")
        if not sep or not key:
            raise ValueError("--set expects key=value: %r" % item)
        try:
            out[key] = json.loads(raw)
        except ValueError:
            out[key] = raw
    return out


def read_state_file(path: Optional[str]) -> Optional[dict]:
    """Dry-run previous state; normalized like the online marker (v1 -> None)."""
    if not path:
        return None
    with open(path, "r", encoding="utf-8") as f:
        return normalize_state(json.load(f))


def reviewed_files(chunks: List[dict]) -> List[str]:
    out: List[str] = []
    for c in chunks:
        out.extend(p for p in c["files"] if p not in out)
    return out


def append_step_summary(env: Dict[str, str], results: List[dict]) -> None:
    path = env.get("GITHUB_STEP_SUMMARY")
    if path and results:
        with open(path, "a", encoding="utf-8") as f:
            f.write("### AI review chunks\n\n" + render_step_summary(results) + "\n")


def default_out_dir(env: Dict[str, str]) -> str:
    rt = env.get("RUNNER_TEMP")
    return os.path.join(rt, "ai-review") if rt else os.path.abspath(".ai-review-out")
