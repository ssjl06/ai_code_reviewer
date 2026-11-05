"""Run one `claude -p` process per diff chunk, with timeouts and a conclusion-only resume."""
from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from review.claude_cmd import build_command, build_env
from review.hooks.guard import DENY_STOP_FILE, STOP_FILE, TIME_STOP_FILE
from review.korean import is_korean, merge_korean
from review.prompt import FALLBACK_PROMPT, KOREAN_PROMPT
from review.stream_log import StreamLogger
from review.validate import extract_output

TIME_RESERVE_S = 300  # exploring stops this long before the chunk limit: one LLM call in flight
MIN_CHUNK_S = 300
MIN_START_S = 60  # a chunk is not started with less than this left before the deadline
KOREAN_GRACE_S = 300  # Korean re-asks may run this long past the deadline (the job allows 20 min)


def run_process(cmd, env, cwd, stdin_text, timeout_s, logger, tee_path=None, info=None) -> str:
    """Return "exited" or "timeout". `info` (dict) receives "rc" and "stderr" (last 500 chars)."""
    proc = subprocess.Popen(cmd, env=env, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8",
                            errors="replace", start_new_session=True)
    err: List[str] = []
    tee = open(tee_path, "a", encoding="utf-8") if tee_path else None

    def pump_out():
        logged = False
        for line in proc.stdout:
            if tee:
                tee.write(line)
                tee.flush()
            try:
                logger.feed(line)
            except Exception as e:  # a parser bug must not stop draining stdout
                if not logged:
                    logged = True
                    print("[runner] stream parse error: %s: %s" % (type(e).__name__, e),
                          file=sys.stderr, flush=True)

    threads = [threading.Thread(target=pump_out, daemon=True),
               threading.Thread(target=lambda: err.extend(proc.stderr), daemon=True)]
    for t in threads:
        t.start()
    def feed_stdin():
        try:
            proc.stdin.write(stdin_text)
        except (BrokenPipeError, OSError, ValueError):
            pass
        try:
            proc.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass

    threading.Thread(target=feed_stdin, daemon=True).start()
    status = "exited"
    try:
        proc.wait(max(timeout_s, 0.01))
    except subprocess.TimeoutExpired:
        status = "timeout"
    try:  # kill the whole group on every path so lingering grandchildren free the pipes
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    proc.wait()
    for t in threads:
        t.join(5)
    if tee:
        tee.close()
    proc.stdout.close()
    proc.stderr.close()
    if info is not None:
        info.update(rc=proc.returncode, stderr="".join(err)[-500:])
    return status


def _result(index, status, output, stats, sid, error):
    return {"index": index, "status": status, "output": output, "stats": stats,
            "session_id": sid, "error": error}


def _empty_stats() -> Dict[str, Any]:
    return StreamLogger("-", out=sys.stdout).stats()


def _finish(idx, out, stats, sid, error):
    """Only an explicit boolean `diff_reviewed: true` makes a chunk ok; false, or a missing or
    non-boolean value (logged separately), makes it partial: its findings are used but its lines
    stay unreviewed. `incomplete_reason` is only a note for the PR body."""
    if out.get("diff_reviewed_invalid"):
        return _result(idx, "partial", out, stats, sid, "diff_reviewed missing")
    if out.get("diff_reviewed") is not True:
        return _result(idx, "partial", out, stats, sid, "diff not reviewed")
    return _result(idx, "ok", out, stats, sid, error)


def _reserve(limit):
    """Seconds before `limit` that exploring stops; a short limit keeps 3/4 of it to explore."""
    return min(TIME_RESERVE_S, limit // 4)


def chunk_limit(cfg: dict, n_chunks: int) -> int:
    """Seconds one chunk may take so `n_chunks` fit the deadline, a resume fallback included."""
    rounds = max(1, math.ceil(n_chunks / cfg["concurrency"]))
    share = cfg["deadline_s"] // rounds - cfg["fallback_timeout_s"]
    return max(MIN_CHUNK_S, min(cfg["chunk_timeout_s"], share))


def _cause(st, stats, state=None):
    """Why an attempt ended without output (pass `state` to detect a guard stop)."""
    res = stats.get("result") or {}
    if st == "timeout":
        return "timeout"
    if state and os.path.exists(os.path.join(state, TIME_STOP_FILE)):
        return "time"
    if state and os.path.exists(os.path.join(state, STOP_FILE)):
        return "budget"
    if state and os.path.exists(os.path.join(state, DENY_STOP_FILE)):
        return "denied"
    if res.get("subtype") == "error_max_turns":
        return "max_turns"
    text = res.get("result")
    bad = res.get("structured_output") is not None or "{" in (text if isinstance(text, str) else "")
    return "invalid output" if bad else "no structured output"


def _report(r):
    """One Actions log line per chunk that did not end ok."""
    if r["status"] != "ok":
        StreamLogger("c%d" % (r["index"] + 1), out=sys.stdout).note(
            "✖ %s  %s" % (r["status"], r["error"]))
    return r


def _denial_summary(state):
    """One line counting the chunk's denied calls by command or tool, or None when there are none."""
    counts: Dict[str, int] = {}
    try:
        with open(os.path.join(state, "guard-audit.jsonl"), encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    if not isinstance(rec, dict) or rec.get("decision") not in ("deny", "stop"):
                        continue
                    tool, inp = rec.get("tool"), rec.get("input")
                    if tool == "Bash":
                        words = str((inp or {}).get("command") or "").split()
                        key = words[0] if words else "Bash"
                    else:
                        key = tool if isinstance(tool, str) and tool else "?"
                        if any(m in str(rec.get("reason")) for m in ("저장소 밖", "outside repo")):
                            key += "(저장소 밖)"
                except (ValueError, AttributeError):
                    continue
                counts[key] = counts.get(key, 0) + 1
    except OSError:
        return None
    if not counts:
        return None
    parts = ["%s×%d" % kv for kv in sorted(counts.items(), key=lambda kv: -kv[1])]
    return "거부된 호출 %d건: %s" % (sum(counts.values()), " ".join(parts))


def run_chunk(chunk, prompt, total, cfg, paths, base_env, timeout_s=None, deadline=None):
    try:
        return _run_chunk(chunk, prompt, total, cfg, paths, base_env, timeout_s, deadline)
    finally:
        try:
            line = _denial_summary(os.path.join(paths["work"], "chunk-%d" % (chunk["index"] + 1),
                                                "state"))
            if line:
                StreamLogger("c%d" % (chunk["index"] + 1), out=sys.stdout).note(line)
        except Exception:  # a log line must never fail the chunk
            pass


def _chunk_env(chunk, cfg, paths, base_env):
    """The chunk's child env and state dir; a resume needs the config dir of its session."""
    cdir = os.path.join(paths["work"], "chunk-%d" % (chunk["index"] + 1))
    state, conf = os.path.join(cdir, "state"), os.path.join(cdir, "config")
    for d in (state, conf):
        os.makedirs(d, exist_ok=True)
    return build_env(cfg, paths["repo"], state, conf, base_env), state


def _attempt(chunk, total, paths, cmd, text, limit, env):
    """One claude call; returns (status, output, session id, stats, process info)."""
    idx = chunk["index"]
    log, info = StreamLogger("c%d" % (idx + 1), out=sys.stdout), {}
    log.start("chunk %d/%d  files=%d  diff=%.1fk tok"
              % (idx + 1, total, len(chunk["files"]), chunk["est_tokens"] / 1000.0))
    tee = os.path.join(paths["work"], "chunk-%d" % (idx + 1), "stream.jsonl")
    st = run_process(cmd, env, paths["repo"], text, limit, log, tee, info)
    out = extract_output(log.stats()["result"])
    log.finish("ok" if out else st, info["stderr"] or None)
    return st, out, log.session_id, log.stats(), info


def _run_chunk(chunk, prompt, total, cfg, paths, base_env, timeout_s=None, deadline=None):
    idx, t0 = chunk["index"], time.monotonic()
    env, state = _chunk_env(chunk, cfg, paths, base_env)

    def attempt(cmd, text, limit, run_env=env):
        st, out, sid, stats, info = _attempt(chunk, total, paths, cmd, text, limit, run_env)
        stats["wall_s"] = time.monotonic() - t0
        return st, out, sid, stats, info

    limit = cfg["chunk_timeout_s"] if timeout_s is None else timeout_s
    end = t0 + limit + cfg["fallback_timeout_s"]  # shared by the first attempt and the conclusion
    if deadline is not None:
        end = min(end, deadline)
        limit = min(limit, end - t0)
    # Only the exploring session gets the time stop: resumes must be able to answer.
    timed = dict(env, AI_REVIEW_STOP_AT=str(time.time() + limit - _reserve(limit)))
    st, out, sid, stats, info = attempt(build_command(cfg, paths["agents_file"]), prompt, limit,
                                        timed)
    error = "timeout" if st == "timeout" else None
    if out:
        return _finish(idx, out, stats, sid, error)
    if not sid:
        msg = error or info["stderr"] or "no output (exit %s)" % info["rc"]
        return _result(idx, "failed", None, stats, None, msg)
    error = _cause(st, stats, state)  # the guard-stopped session is resumed like any other
    limit = end - time.monotonic()
    if limit <= 0.5:
        return _result(idx, "incomplete", None, stats, sid, error + " → deadline")
    st2, out, sid2, stats2, _ = attempt(build_command(cfg, paths["agents_file"], sid),
                                        FALLBACK_PROMPT, limit)
    if out:  # the Korean pass must resume the session that holds this answer
        return _finish(idx, out, stats2, sid2 or sid, error)
    return _result(idx, "incomplete", None, stats2, sid, "%s → %s" % (error, _cause(st2, stats2)))


def _reask_korean(r, chunk, total, cfg, paths, base_env, korean_end):
    """Re-ask a non-Korean output once on its session. Whatever happens the result keeps its
    status, and its output unless a usable Korean answer replaces the prose."""
    note = StreamLogger("c%d" % (r["index"] + 1), out=sys.stdout).note
    try:
        if not r.get("output") or is_korean(r["output"]):
            return r
        if not r.get("session_id"):
            note("세션 없음 — 한국어 재요청 생략, 원문 유지")
            return r
        limit = min(cfg["fallback_timeout_s"], korean_end - time.monotonic())
        if limit <= 0.5:
            note("시간 부족 — 한국어 재요청 생략, 원문 유지")
            return r
        t0 = time.monotonic()
        env, _ = _chunk_env(chunk, cfg, paths, base_env)
        new = _attempt(chunk, total, paths,
                       build_command(cfg, paths["agents_file"], r["session_id"]), KOREAN_PROMPT,
                       limit, env)[1]
        merged = merge_korean(r["output"], new)
        note("한국어로 다시 받음" if merged else "한국어 재요청 실패 — 원문 유지")
        stats = r.get("stats") or {}
        return dict(r, output=merged or r["output"],
                    stats=dict(stats, wall_s=stats.get("wall_s", 0) + time.monotonic() - t0))
    except Exception as e:  # a translation must never fail the chunk
        note("한국어 재요청 실패 — 원문 유지 (%s: %s)" % (type(e).__name__, e))
        return r


def _korean_pass(results, chunks, cfg, paths, base_env, deadline):
    """After every chunk: re-ask the non-Korean outputs, up to KOREAN_GRACE_S past the deadline."""
    by_index = {c["index"]: c for c in chunks}
    korean_end = deadline + KOREAN_GRACE_S

    def one(r):
        return _reask_korean(r, by_index[r["index"]], len(chunks), cfg, paths, base_env, korean_end)

    with ThreadPoolExecutor(cfg["concurrency"]) as ex:
        return list(ex.map(one, results))


def run_all(chunks, prompts, cfg, paths, base_env, deadline) -> List[dict]:
    limit = chunk_limit(cfg, len(chunks))

    def one(chunk, prompt):
        left = deadline - time.monotonic()
        if left < MIN_START_S:
            return _report(_result(chunk["index"], "incomplete", None, _empty_stats(), None,
                                   "deadline"))
        try:
            return _report(run_chunk(chunk, prompt, len(chunks), cfg, paths, base_env,
                                     min(limit, left), deadline))
        except Exception as e:  # one broken chunk must not discard the others
            return _report(_result(chunk["index"], "failed", None, _empty_stats(), None,
                                   "%s: %s" % (type(e).__name__, e)))

    with ThreadPoolExecutor(cfg["concurrency"]) as ex:
        futs = [ex.submit(one, c, p) for c, p in zip(chunks, prompts)]
        results = sorted((f.result() for f in futs), key=lambda r: r["index"])
    return _korean_pass(results, chunks, cfg, paths, base_env, deadline)
