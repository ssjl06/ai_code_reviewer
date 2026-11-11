"""Commit a guideline change as the bot and push it to the PR branch.

The PAT reaches git only through GIT_CONFIG_* variables in the child environment of the
commands that talk to the remote: never argv, never .git/config."""
from __future__ import annotations

import base64
import os
import re
import subprocess
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import urlsplit

from .failure import log
from .gitutil import GitError
from .rules import RULES_DIR

TRAILER = "AI-Review-Mention"
MIN_GIT = (2, 31)
ERR_OLD_GIT = "git 2.31 이상이 필요합니다"
ERR_PUSH = "브랜치에 push하지 못했습니다"


def _run(repo: str, args, env: Optional[Dict[str, str]] = None, timeout: int = 120) -> str:
    try:
        proc = subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise GitError("git %s timed out after %ss" % (args[0], e.timeout))
    if proc.returncode != 0:
        raise GitError(proc.stderr)
    return proc.stdout


def git_ok(repo: str) -> bool:
    try:
        m = re.search(r"(\d+)\.(\d+)", _run(repo, ["--version"], timeout=30))
    except (GitError, OSError):
        return False
    return bool(m) and (int(m.group(1)), int(m.group(2))) >= MIN_GIT


def origin_of(url: str) -> str:
    """Scheme and host of an http(s) remote URL; "" for anything else (file path, ssh)."""
    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    host = parts.hostname + (":%d" % parts.port if parts.port else "")
    return "%s://%s" % (parts.scheme, host)


def auth_env(pat: str, origin: str, base_env: Dict[str, str]) -> Dict[str, str]:
    """Child environment carrying the PAT as an extra header for `origin` only.
    An empty `origin` (non-http remote) gets no header at all."""
    env = {k: v for k, v in base_env.items()
           if not k.startswith("GIT_TRACE") and k != "GIT_CURL_VERBOSE"}
    env["GIT_TERMINAL_PROMPT"] = "0"
    if origin:
        token = base64.b64encode(("x-access-token:" + pat).encode("utf-8")).decode("ascii")
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.%s/.extraheader" % origin
        env["GIT_CONFIG_VALUE_0"] = "AUTHORIZATION: basic " + token
    return env


def _has_trailer(body: str, key: str) -> bool:
    return (TRAILER + ": " + key) in body.splitlines()


def receipt(repo: str, base: str, head: str, key: str) -> Optional[str]:
    """Subject of a commit in base..head whose message has the trailer line for exactly `key`."""
    out = _run(repo, ["log", "--format=%s%x1f%b%x1e", "%s..%s" % (base, head)])
    for rec in out.split("\x1e"):
        subject, _, body = rec.lstrip("\n").partition("\x1f")
        if _has_trailer(body, key):
            return subject
    return None


def _commit(repo: str, message: str, key: str, author: Tuple[str, str]) -> bool:
    """Stage the guideline directory and commit; False when nothing changed."""
    _run(repo, ["add", "-A", "--", RULES_DIR])
    if not _run(repo, ["diff", "--cached", "--name-only", "--", RULES_DIR]).strip():
        return False
    env = dict(os.environ, GIT_AUTHOR_NAME=author[0], GIT_AUTHOR_EMAIL=author[1],
               GIT_COMMITTER_NAME=author[0], GIT_COMMITTER_EMAIL=author[1])
    _run(repo, ["-c", "commit.gpgsign=false", "commit", "--no-verify", "-q", "-m",
                "%s\n\n%s: %s" % (message, TRAILER, key)], env=env)
    return True


def _fetch_tip(repo: str, remote_url: str, branch: str, env: Dict[str, str]) -> str:
    _run(repo, ["fetch", "--quiet", remote_url, "+refs/heads/%s" % branch], env=env)
    return _run(repo, ["rev-parse", "FETCH_HEAD^{commit}"]).strip()


def push_change(repo: str, remote_url: str, branch: str,
                apply: Callable[[str], Optional[dict]], message: str, key: str,
                author: Tuple[str, str], pat: str) -> Tuple[Optional[dict], Optional[str]]:
    if not git_ok(repo):
        return None, ERR_OLD_GIT
    env = auth_env(pat, origin_of(remote_url), dict(os.environ))
    start = _run(repo, ["rev-parse", "HEAD"]).strip()

    def fail(err: object) -> Tuple[None, str]:
        log("지침 push 실패: %s" % (str(err).replace(pat, "***") if pat else err))
        try:
            _run(repo, ["reset", "--hard", "-q", start])
            _run(repo, ["clean", "-fdq", "--", RULES_DIR])
        except GitError:
            pass
        return None, ERR_PUSH

    try:
        result = apply(repo)
        if result is None or not _commit(repo, message, key, author):
            return result, None
        for attempt in (1, 2):
            try:
                _run(repo, ["push", "--quiet", remote_url, "HEAD:refs/heads/" + branch], env=env)
                return result, None
            except GitError as e:
                err = e
            tip = _fetch_tip(repo, remote_url, branch, env)
            body = _run(repo, ["log", "-1", "--format=%b", tip])
            if _has_trailer(body, key):  # the push landed although git reported an error
                _run(repo, ["checkout", "--detach", "-q", "-f", tip])
                return result, None
            if attempt == 2 or subprocess.run(
                    ["git", "merge-base", "--is-ancestor", tip, "HEAD"], cwd=repo).returncode == 0:
                return fail(err)  # not a race: the remote did not move past our base
            # the branch moved: redo the change on top of its tip
            _run(repo, ["checkout", "--detach", "-q", "-f", tip])
            result = apply(repo)
            if result is None or not _commit(repo, message, key, author):
                return result, None
        return fail("unreachable")
    except (GitError, OSError) as e:
        return fail(e)
