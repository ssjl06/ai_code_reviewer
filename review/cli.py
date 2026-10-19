"""CLI: PR + previous bot state -> scope -> chunks -> claude per chunk -> verdict -> post."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import List, Optional

from review import scope as sc
from review.aggregate import merge, split_inline
from review.claude_cmd import write_agents_file
from review.claude_runner import run_all
from review.config import KIT_DIR, load_config
from review.diff_split import build_chunks, line_map
from review.failure import log, post, report_failure
from review.github_api import GitHub, GitHubError
from review.gitutil import GitError
from review.prompt import build_prompt
from review.render import render_body, render_inline
from review.validate import normalize_state

LLM_ENV = ("AI_REVIEW_LLM_BASE_URL", "AI_REVIEW_LLM_API_KEY")
ONLINE_ENV = ("GITHUB_API_URL", "GITHUB_REPOSITORY", "AI_REVIEW_BOT_PAT", "AI_REVIEW_BOT_NAME")
AGENT_MD = os.path.join(KIT_DIR, "agent", "pr-reviewer.md")


def _fail(msg: str) -> int:
    print("ai-review: error: " + msg, file=sys.stderr, flush=True)
    return 2


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python3 -m review", description="AI PR review")
    p.add_argument("--repo", default=".", help="checked-out PR repository")
    p.add_argument("--base", help="base sha (default: PR base)")
    p.add_argument("--head", help="head sha (default: PR head)")
    p.add_argument("--pr", type=int, help="pull request number (online mode)")
    p.add_argument("--dry-run", action="store_true", help="print the review, post nothing")
    p.add_argument("--config", help="extra config JSON file")
    p.add_argument("--set", action="append", default=[], metavar="KEY=JSON",
                   help="config override (repeatable)")
    p.add_argument("--out-dir", help="work dir (default $RUNNER_TEMP/ai-review)")
    p.add_argument("--state-file", help="dry-run: previous State JSON")
    return p


def _missing(args, env) -> List[str]:
    need = [k for k in LLM_ENV if not env.get(k)]
    if args.dry_run:
        return need + ["--" + k for k in ("base", "head") if not getattr(args, k)]
    return need + [k for k in ONLINE_ENV if not env.get(k)] + ([] if args.pr else ["--pr"])


def _inputs(args, env):
    """Return (gh, base, head, title, prev)."""
    if args.dry_run:
        return None, args.base, args.head, "", normalize_state(sc.read_state_file(args.state_file))
    gh = GitHub(env["GITHUB_API_URL"], env["AI_REVIEW_BOT_PAT"], env["GITHUB_REPOSITORY"], args.pr)
    pr = gh.get_pr()
    base, head = args.base or pr["base"]["sha"], args.head or pr["head"]["sha"]
    return gh, base, head, pr.get("title") or "", normalize_state(
        gh.latest_state(env["AI_REVIEW_BOT_NAME"]))


def main(argv: Optional[List[str]] = None) -> int:
    t0 = time.monotonic()
    args = _parser().parse_args(argv)
    env = dict(os.environ)
    try:
        cfg = load_config(args.config, sc.parse_overrides(args.set))
    except (KeyError, ValueError, OSError) as e:
        return _fail("invalid config: %s" % e)
    missing = _missing(args, env)
    if missing:
        return _fail("missing required environment/arguments: " + ", ".join(missing))
    deadline = t0 + cfg["deadline_s"]
    out_dir = os.path.abspath(args.out_dir or sc.default_out_dir(env))
    repo = os.path.abspath(args.repo)
    try:
        gh, base, head, title, prev = _inputs(args, env)
        scope = sc.determine(repo, base, head, prev)
    except GitHubError as e:
        log("GitHub 조회 실패: %s" % e)
        return 1
    except (GitError, OSError, ValueError) as e:
        print("ai-review: error: %s" % e, file=sys.stderr, flush=True)
        return 1
    try:
        return _review(args, env, cfg, t0, deadline, out_dir, repo, gh, head, title, prev, scope)
    except Exception as e:  # unexpected bug: never leave the PR without a comment
        return report_failure(gh, head, e)


def _review(args, env, cfg, t0, deadline, out_dir, repo, gh, head, title, prev, scope) -> int:
    if scope["mode"] == "unchanged":
        log("이미 리뷰한 커밋 %s — 리뷰를 건너뜀" % head[:7])
        return 0
    mode_ko = {"full": "전체 리뷰", "incremental": "증분 리뷰"}[scope["mode"]]
    log("리뷰 범위: %s (%s, 대상 파일 %d개)" % (mode_ko, scope["range"], len(scope["targets"])))
    os.makedirs(out_dir, exist_ok=True)
    chunks, skipped = build_chunks(scope["targets"], cfg)
    log("조각 %d개 · 생략 파일 %d개" % (len(chunks), len(skipped)))
    previous = (prev or {}).get("findings") or []
    results: List[dict] = []
    if chunks:
        ctx = {"chunk_total": len(chunks), "mode": scope["mode"], "range": scope["range"],
               "pr_title": title, "previous": previous, "explore_budget": cfg["explore_budget"]}
        prompts = [build_prompt(c, ctx) for c in chunks]
        paths = {"repo": repo, "work": out_dir,
                 "agents_file": write_agents_file(AGENT_MD, out_dir)}
        results = run_all(chunks, prompts, cfg, paths, env, deadline)
    review = merge(results, cfg, previous, scope["changed"], scope["delta_files"],
                   no_targets=not chunks, skipped=skipped)
    inline, body_only = split_inline(review["findings"],
                                     line_map(scope["pr_files"], "right_lines"))
    state = sc.next_state(head, review, prev)
    meta = {"model": env.get("AI_REVIEW_MODEL") or cfg["model"], "chunks": len(chunks),
            "duration_s": time.monotonic() - t0, "range": scope["range"], "mode": scope["mode"],
            "reviewed": sc.reviewed_files(chunks), "skipped": skipped}
    body = render_body(review, body_only, meta, state)
    fallback = render_body(review, inline + body_only, meta, state)
    comments = [{"path": f["file"], "line": f["line"], "side": "RIGHT", "body": render_inline(f)}
                for f in inline]
    log("판정: %s · 새 지적 %d건 (inline %d) · 미완료 조각 %d개" % (
        review["event"], len(review["findings"]), len(inline), len(review["incomplete"])))
    sc.append_step_summary(env, results)
    payload = {"event": review["event"], "body": body, "comments": comments}
    with open(os.path.join(out_dir, "review.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
        log("dry-run: 게시하지 않음")
        return 0
    return post(gh, env, head, review, body, comments, fallback)


if __name__ == "__main__":
    sys.exit(main())
