"""CLI: PR + previous bot state -> scope -> chunks -> claude per chunk -> verdict -> post."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Dict, List, Optional

from review import cli_util as cu
from review import rules
from review import scope as sc
from review.aggregate import merge, split_inline
from review.claude_cmd import write_agents_file
from review.claude_runner import run_all
from review.config import KIT_DIR, load_config
from review.diff_split import build_chunks, line_map
from review.failure import log, post, report_failure
from review.github_api import GitHub, GitHubError
from review.gitutil import GitError
from review.ledger import apply_waivers, assign_recheck, pending_after, prompt_lists, shown_lines
from review.previous import anchor_of
from review.prompt import build_prompt, orphan_split
from review.render import issue_marker, parse_state, render_body, render_inline
from review.validate import normalize_state

LLM_ENV = ("AI_REVIEW_LLM_BASE_URL", "AI_REVIEW_LLM_API_KEY")
ONLINE_ENV = ("GITHUB_API_URL", "GITHUB_REPOSITORY", "AI_REVIEW_BOT_PAT", "AI_REVIEW_BOT_NAME")
AGENT_MD = os.path.join(KIT_DIR, "agent", "pr-reviewer.md")
# The state marker of the review this process posted last (`main` resets it): the mention
# phase starts from it, as a listing right after the post may not show that review yet.
LAST_POSTED: Optional[dict] = None


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
        return None, args.base, args.head, "", cu.read_state_file(args.state_file)
    gh = GitHub(env["GITHUB_API_URL"], env["AI_REVIEW_BOT_PAT"], env["GITHUB_REPOSITORY"], args.pr)
    pr = gh.get_pr()
    base, head = args.base or pr["base"]["sha"], args.head or pr["head"]["sha"]
    bot = env["AI_REVIEW_BOT_NAME"]
    prev = normalize_state(gh.latest_state(bot))
    return gh, base, head, pr.get("title") or "", apply_waivers(prev, gh.dismissed_ids(bot))


def main(argv: Optional[List[str]] = None) -> int:
    global LAST_POSTED
    LAST_POSTED = None
    t0 = time.monotonic()
    args = _parser().parse_args(argv)
    env = dict(os.environ)
    try:
        cfg = load_config(args.config, cu.parse_overrides(args.set))
    except (KeyError, ValueError, OSError) as e:
        return _fail("invalid config: %s" % e)
    missing = _missing(args, env)
    if missing:
        return _fail("missing required environment/arguments: " + ", ".join(missing))
    deadline = t0 + cfg["deadline_s"]
    out_dir = os.path.abspath(args.out_dir or cu.default_out_dir(env))
    repo = os.path.abspath(args.repo)
    try:
        gh, base, head, title, prev = _inputs(args, env)
        scope = sc.determine(repo, base, head, prev, cfg)
    except GitHubError as e:
        log("GitHub 조회 실패: %s" % e)
        return 1
    except (GitError, OSError, ValueError) as e:
        print("ai-review: error: %s" % e, file=sys.stderr, flush=True)
        return 1
    try:
        return _review(args, env, cfg, t0, deadline, out_dir, repo, gh, head, title, scope)
    except Exception as e:  # unexpected bug: never leave the PR without a comment
        return report_failure(gh, head, e)


def _pending(review: dict, ids: List[str]) -> List[str]:
    """Pending ids of issues that are still open after this run (a closed one needs no recheck)."""
    live = {p["id"] for p in review["previous"] if p["status"] in ("open", "unclear")}
    return [i for i in ids if i in live]


WEAK_MSG = "이전 커밋을 찾을 수 없어 위치 추적이 약해짐"


def _log_left_out(orphan_ids: List[str], recheck_ids: List[str]) -> None:
    """Orphans the prompt leaves out (list cap) and rechecks that did not fit."""
    if orphan_ids:
        log("고아 이슈 %d건은 프롬프트 목록에서 제외됨: %s" % (len(orphan_ids), ", ".join(orphan_ids)))
    if recheck_ids:
        log("다시 확인할 이슈 %d건을 이번 실행에 담지 못함 (크기 제한 또는 조각 제한, 다음 실행에서 다시 확인): %s"
            % (len(recheck_ids), ", ".join(recheck_ids)))


def _log_cut(review: dict, recheck: dict) -> None:
    """Open issues whose recheck item had a region line too long to show whole: their
    `resolved` report cannot count, and no later run can show more, so they are not pending."""
    live = {p["id"] for p in review["previous"] if p["status"] in ("open", "unclear")}
    pending = set(review["recheck_pending"])
    for items in recheck.values():
        for i in items:
            if i.get("region_cut") and i["id"] in live and i["id"] not in pending:
                log("줄이 너무 길어 재검토 불가: %s" % i["id"])


def _cut_files(scope: dict, chunks: List[dict]) -> List[str]:
    """Files with a line too long for a chunk: cut in this run (any chunk) or stored as cut and
    still in the head. Each is logged: with the line when it was cut in this run."""
    rows = [(p, n) for c in chunks for p, lines in (c.get("cut") or {}).items() for n in lines]
    for p, n in rows:
        log("줄이 너무 길어 일부만 검토함: %s:%d" % (p, n))
    now = {p for p, _ in rows}
    for p in scope.get("cut_files") or ():
        if p not in now:
            log("줄이 너무 길어 일부만 검토함: %s" % p)
    return sorted(now | set(scope.get("cut_files") or ()))


def _log_matches(review: dict) -> None:
    """Each finding swallowed by the ledger, each link the model made (and the resolved
    report it overrode), and each new finding kept next to a known issue (open or closed)."""
    for d in review.get("duplicates") or []:
        if d.get("folded"):
            continue  # logged below with its detail
        log("기존 이슈와 중복되어 생략: %s:%s (이슈 %s)" % (d["file"], d["line"], d["dup_of"]))
    for d in review.get("folded") or []:
        log("기존 이슈에 합쳐진 지적: %s:%s %s (%s) %s — %s" % (
            d["file"], d["line"], d["severity"], d["dup_of"], d["title"], d["detail"]))
    for d in review.get("overlap") or []:
        log("겹치는 조각의 같은 지적 생략: %s:%s %s" % (d["file"], d["line"], d["title"]))
    for x in review.get("links") or []:
        log("기존 이슈에 연결: %s:%s (이슈 %s)" % (x["file"], x["line"], x["id"]))
    for x in (review.get("links") or []) + (review.get("evidence") or []):
        if x.get("reopened"):
            log("같은 문제가 다시 지적되어 해결 보고를 무시: %s:%s (이슈 %s)"
                % (x["file"], x["line"], x["id"]))
    for f in (review.get("findings") or []) + (review.get("duplicates") or []):
        if f.get("same_as_ignored"):
            log("same_as 무시: 지정한 이슈보다 심각함 — %s:%s (이슈 %s)"
                % (f["file"], f["line"], f["same_as_ignored"]))
    for f in review.get("findings") or []:
        if f.get("near_issue"):
            log("기존 이슈 근처의 새 지적: %s:%s (이슈 %s)"
                % (f["file"], f["line"], f["near_issue"]))
        if f.get("near_closed"):
            log("해결/면제된 이슈 근처의 새 지적: %s:%s (이슈 %s)"
                % (f["file"], f["line"], f["near_closed"]))


def _chunk_rules(repo: str, chunks: List[dict], cfg: dict) -> Dict[int, list]:
    """{chunk index: [(path, text)]} of the guideline files that apply; omitted ones are logged."""
    docs = rules.load_docs(repo)
    out: Dict[int, list] = {}
    for c in chunks:
        chosen, omitted = rules.select(docs, c["files"], cfg["rules_max_chars"])
        out[c["index"]] = chosen
        if omitted:
            log("생략된 지침: " + ", ".join(omitted))
    return out


def _review(args, env, cfg, t0, deadline, out_dir, repo, gh, head, title, scope) -> int:
    if scope["mode"] == "unchanged":
        log("새로 리뷰할 변경 없음 — 리뷰를 건너뜀")
        return 0
    settle = scope["mode"] == "settle"  # a new waiver on a fully reviewed head: no model call
    mode_ko = {"full": "전체 리뷰", "incremental": "증분 리뷰", "settle": "면제 반영"}[scope["mode"]]
    log("리뷰 범위: %s (%s, 대상 파일 %d개)" % (mode_ko, scope["range"], len(scope["targets"])))
    os.makedirs(out_dir, exist_ok=True)
    chunks, skipped, truncated = build_chunks(scope["targets"], cfg)
    log("조각 %d개 · 생략 파일 %d개 · 잘린 파일 %d개" % (len(chunks), len(skipped), len(truncated)))
    if scope["weak_tracking"]:
        log(WEAK_MSG)
    previous = scope["previous"]
    recheck, left_out = assign_recheck(chunks, previous, scope["blobs"], cfg)
    only = {t["path"] for t in scope["targets"] if t.get("recheck_only")}
    ledger, orphans = prompt_lists(previous, scope["closed"])
    _log_left_out(orphan_split(orphans)[1], left_out)
    results: List[dict] = []
    if chunks:
        ctx = {"chunk_total": len(chunks), "mode": scope["mode"], "range": scope["range"],
               "pr_title": title, "previous": previous, "ledger": ledger, "orphans": orphans,
               "recheck": recheck,
               "added": scope["added"], "deleted": scope["deleted"], "explore_budget": cfg["explore_budget"]}
        ctx["rules"] = _chunk_rules(repo, chunks, cfg)
        prompts = [build_prompt(c, ctx) for c in chunks]
        paths = {"repo": repo, "work": out_dir,
                 "agents_file": write_agents_file(AGENT_MD, out_dir)}
        results = run_all(chunks, prompts, cfg, paths, env, deadline)
    review = merge(results, cfg, previous, scope["changed"],
                   scope["reviewable"] == 0 and not settle,
                   skipped=skipped, anchor=anchor_of(scope["blobs"]), truncated=truncated,
                   chunk_files={c["index"]: c["files"] for c in chunks},
                   closed=scope["closed"], shown=shown_lines(chunks, recheck),
                   recheck_only=only, left_out=left_out, cut=_cut_files(scope, chunks))
    review["recheck_pending"] = _pending(review, pending_after(
        recheck, left_out, results, chunks, skipped, truncated, only))
    _log_cut(review, recheck)
    review["waived_now"] = int((scope["prev"] or {}).get("waived_now") or 0)
    _log_matches(review)
    inline, body_only = split_inline(review["findings"],
                                     line_map(scope["pr_files"], "right_lines"))
    state = (sc.settle_state(head, review, scope) if settle
             else sc.next_state(head, review, scope, chunks, results))
    meta = {"model": env.get("AI_REVIEW_MODEL") or cfg["model"], "chunks": len(chunks),
            "duration_s": time.monotonic() - t0, "range": scope["range"], "mode": scope["mode"],
            "reviewed": cu.reviewed_files(chunks), "skipped": skipped}
    body = render_body(review, body_only, meta, state)
    fallback = render_body(review, inline + body_only, meta, state)
    comments = [{"path": f["file"], "line": f["line"], "side": "RIGHT",
                 "body": render_inline(f) + issue_marker(f["id"])} for f in inline]
    log("판정: %s · 새 지적 %d건 (inline %d) · 미완료 조각 %d개" % (
        review["event"], len(review["findings"]), len(inline), len(review["incomplete"])))
    cu.append_step_summary(env, results)
    payload = {"event": review["event"], "body": body, "comments": comments}
    with open(os.path.join(out_dir, "review.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
        log("dry-run: 게시하지 않음")
        return 0
    def posted() -> None:
        global LAST_POSTED
        LAST_POSTED = parse_state(body)
    return post(gh, env, head, review, body, comments, fallback, posted)


if __name__ == "__main__":
    sys.exit(main())
