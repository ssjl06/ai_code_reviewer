"""Run one `claude -p` process per diff chunk, with timeouts and a conclusion-only resume."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from review.claude_cmd import build_command, build_env
from review.prompt import FALLBACK_PROMPT
from review.stream_log import StreamLogger


def extract_output(result: Optional[dict]) -> Optional[dict]:
    if not isinstance(result, dict):
        return None
    if isinstance(result.get("structured_output"), dict):
        return result["structured_output"]
    text, dec, i = result.get("result"), json.JSONDecoder(), 0
    if not isinstance(text, str):
        return None
    while True:
        i = text.find("{", i)
        if i < 0:
            return None
        try:
            obj, end = dec.raw_decode(text, i)
            if isinstance(obj, dict) and "findings" in obj:
                return obj
            i = end if isinstance(obj, dict) else i + 1
        except ValueError:
            i += 1


def run_process(cmd, env, cwd, stdin_text, timeout_s, logger, tee_path=None, info=None) -> str:
    """Return "exited" or "timeout". `info` (dict) receives "rc" and "stderr" (last 500 chars)."""
    proc = subprocess.Popen(cmd, env=env, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8",
                            errors="replace", start_new_session=True)
    err: List[str] = []
    tee = open(tee_path, "a", encoding="utf-8") if tee_path else None

    def pump_out():
        for line in proc.stdout:
            if tee:
                tee.write(line)
                tee.flush()
            logger.feed(line)

    threads = [threading.Thread(target=pump_out, daemon=True),
               threading.Thread(target=lambda: err.extend(proc.stderr), daemon=True)]
    for t in threads:
        t.start()
    try:
        proc.stdin.write(stdin_text)
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    status = "exited"
    try:
        proc.wait(max(timeout_s, 0.01))
    except subprocess.TimeoutExpired:
        status = "timeout"
        try:
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


def run_chunk(chunk, prompt, total, cfg, paths, base_env, timeout_s=None, deadline=None):
    idx, t0 = chunk["index"], time.monotonic()
    cdir = os.path.join(paths["work"], "chunk-%d" % (idx + 1))
    state, conf = os.path.join(cdir, "state"), os.path.join(cdir, "config")
    for d in (state, conf):
        os.makedirs(d, exist_ok=True)
    env = build_env(cfg, paths["repo"], state, conf, base_env)
    tee = os.path.join(cdir, "stream.jsonl")

    def attempt(cmd, text, limit):
        log, info = StreamLogger("c%d" % (idx + 1), out=sys.stdout), {}
        log.start("chunk %d/%d  files=%d  diff=%.1fk tok"
                  % (idx + 1, total, len(chunk["files"]), chunk["est_tokens"] / 1000.0))
        st = run_process(cmd, env, paths["repo"], text, limit, log, tee, info)
        out = extract_output(log.stats()["result"])
        log.finish("ok" if out else st, info["stderr"] or None)
        stats = log.stats()
        stats["wall_s"] = time.monotonic() - t0
        return st, out, log.session_id, stats, info

    limit = cfg["chunk_timeout_s"] if timeout_s is None else timeout_s
    st, out, sid, stats, info = attempt(build_command(cfg, paths["agents_file"]), prompt, limit)
    error = "timeout" if st == "timeout" else None
    if out:
        return _result(idx, "ok", out, stats, sid, error)
    if not sid:
        msg = error or info["stderr"] or "no output (exit %s)" % info["rc"]
        return _result(idx, "failed", None, stats, None, msg)
    limit = cfg["fallback_timeout_s"]
    if deadline is not None:
        limit = min(limit, deadline - time.monotonic())
    if limit <= 0.5:
        return _result(idx, "incomplete", None, stats, sid, error or "deadline")
    st2, out, _, stats2, _ = attempt(build_command(cfg, paths["agents_file"], sid),
                                     FALLBACK_PROMPT, limit)
    if out:
        return _result(idx, "ok", out, stats2, sid, error)
    return _result(idx, "incomplete", None, stats2, sid, error or "no structured output")


def run_all(chunks, prompts, cfg, paths, base_env, deadline) -> List[dict]:
    def one(chunk, prompt):
        left = deadline - time.monotonic()
        if left <= 0:
            return _result(chunk["index"], "incomplete", None, _empty_stats(), None, "deadline")
        return run_chunk(chunk, prompt, len(chunks), cfg, paths, base_env,
                         min(cfg["chunk_timeout_s"], left), deadline)

    with ThreadPoolExecutor(cfg["concurrency"]) as ex:
        futs = [ex.submit(one, c, p) for c, p in zip(chunks, prompts)]
        return sorted((f.result() for f in futs), key=lambda r: r["index"])
