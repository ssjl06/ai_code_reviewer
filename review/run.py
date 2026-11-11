"""One run (spec 3): the review phase, then the mention phase; the exit code is the higher."""
from __future__ import annotations

import os
import sys
import traceback
from typing import List, Optional

from review import cli
from review import cli_util as cu
from review.config import load_config
from review.failure import log
from review.github_api import GitHub, GitHubError
from review.gitutil import GitError, git
from review.mention import cli as mention_cli


def _skip_reason(pr: dict) -> Optional[str]:
    if pr.get("draft"):
        return "초안"
    if pr.get("state", "open") != "open" or pr.get("merged"):
        return "닫힘"
    return None


def _at_checkout(args, argv: Optional[List[str]]) -> Optional[List[str]]:
    """`argv` with `--head` set to the checked-out commit (spec 3: the head of a run), unless
    it names one already."""
    if args.head:
        return argv
    try:
        head = git(args.repo, "rev-parse", "HEAD").strip()
    except (GitError, OSError) as e:
        log("checkout 커밋 확인 실패: %s" % e)
        return argv
    return list(sys.argv[1:] if argv is None else argv) + ["--head", head]


def main(argv: Optional[List[str]] = None) -> int:
    args = cli._parser().parse_args(argv)
    if args.dry_run:
        return cli.main(argv)
    env = dict(os.environ)
    try:
        cfg = load_config(args.config, cu.parse_overrides(args.set))
    except (KeyError, ValueError, OSError):
        return cli.main(argv)  # the review phase reports the bad config
    if cli._missing(args, env):
        return cli.main(argv)  # ... and the missing inputs
    gh = GitHub(env["GITHUB_API_URL"], env["AI_REVIEW_BOT_PAT"], env["GITHUB_REPOSITORY"], args.pr)
    try:
        pr = gh.get_pr()
    except GitHubError as e:
        log("GitHub 조회 실패: %s" % e)
        return 1
    skip = _skip_reason(pr)
    if skip:
        log("리뷰 단계 건너뜀: %s" % skip)
        rc = 0
    else:
        log("리뷰 단계 실행")
        rc = cli.main(_at_checkout(args, argv))
    hint = None if skip else cli.LAST_POSTED  # the state the review phase just posted
    try:
        rc_mention = mention_cli.run(args, env, cfg, gh, state_hint=hint)
    except Exception as e:  # a bug in the mention phase: logged, the review phase stands
        traceback.print_exception(type(e), e, e.__traceback__, file=sys.stderr)
        sys.stderr.flush()
        log("언급 단계 실패: %s: %s" % (type(e).__name__, e))
        rc_mention = 1
    return max(rc, rc_mention)
