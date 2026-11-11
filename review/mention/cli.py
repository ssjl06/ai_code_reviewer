"""The mention phase (spec 4-6): every pending mention of the PR, oldest first."""
from __future__ import annotations

import json
import os
import time
from typing import List

from review import cli_util as cu
from review.claude_cmd import MENTION, write_agents_file
from review.config import KIT_DIR
from review.failure import log
from review.github_api import GitHubError
from review.gitutil import GitError, git
from review.ledger import apply_waivers
from review.mention import fetch, handle
from review.validate import normalize_state

MENTION_MD = os.path.join(KIT_DIR, "agent", "pr-mention.md")
_OUTCOMES = {"answered": "답변함", "failed": "실패 (다음 실행에서 다시)",
             "stopped": "중단 (head 변경)", None: "처리 안 함"}


def _summary(env: dict, rows: List[dict]) -> None:
    path = env.get("GITHUB_STEP_SUMMARY")
    if not path or not rows:
        return
    lines = ["### AI review mentions", "", "| # | 언급 | 종류 | 명령 | 결과 |",
             "|---|---|---|---|---|"]
    lines += ["| m%d | %s | %s | %s | %s |" % (r["n"], r["key"], r["kind"], r["command"] or "-",
                                             _OUTCOMES[r["outcome"]]) for r in rows]
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n\n")


def run(args, env, cfg, gh) -> int:
    """Answer the pending mentions; 0 when every one got its reply, else 1."""
    deadline = time.monotonic() + cfg["mention_deadline_s"]
    bot = env["AI_REVIEW_BOT_NAME"]
    repo = os.path.abspath(args.repo)
    work = os.path.join(os.path.abspath(args.out_dir or cu.default_out_dir(env)), "mention")
    lists = (gh.list_issue_comments(), gh.list_review_comments(), gh.list_reviews())
    found, ignored = fetch.pending(*lists, bot, cfg["mention_since"], gh.permission)
    for line in ignored:
        print("[m] " + line, flush=True)
    log("언급 %d건 처리" % len(found))
    if not found:
        return 0
    head = git(repo, "rev-parse", "HEAD").strip()
    state = apply_waivers(normalize_state(gh.latest_state(bot)), gh.dismissed_ids(bot))
    os.makedirs(work, exist_ok=True)
    scfg = dict(cfg, explore_budget=cfg["mention_explore_budget"],
                chunk_timeout_s=cfg["mention_session_s"])
    paths = {"repo": repo, "work": work,
             "agents_file": write_agents_file(MENTION_MD, work, MENTION)}
    ph = handle.Phase(repo, env, cfg, scfg, gh, bot, paths, deadline, head, state, lists,
                      [m["author"] for m in found])
    rows = [{"n": n, "key": m["key"], "kind": m["kind"], "command": m.get("command"),
             "outcome": None} for n, m in enumerate(found, 1)]
    for row, m in zip(rows, found):
        t0 = time.monotonic()
        try:
            row["outcome"] = handle.handle(ph, row["n"], m)
        except (GitHubError, GitError, OSError) as e:
            handle.note(row["n"], "처리 실패: %s: %s" % (type(e).__name__, e))
            row["outcome"] = "failed"
        row["seconds"] = round(time.monotonic() - t0, 1)
        if row["outcome"] == "stopped":
            break
    _summary(env, rows)
    with open(os.path.join(work, "mention.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    answered = sum(r["outcome"] == "answered" for r in rows)
    log("언급 %d건 중 %d건 답변" % (len(rows), answered))
    return 0 if answered == len(rows) and not ph.failed else 1
