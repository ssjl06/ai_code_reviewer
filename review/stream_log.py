"""Turn Claude Code stream-json stdout lines into timed, human-readable log lines."""
from __future__ import annotations

import json
import sys
import threading
import time

_LOCK = threading.Lock()
_MAX = 120
_GUARD_MARKERS = ("outside repo", "not allowed", "unparseable command", "guard error", "탐색 예산")


def _trunc(s, n=_MAX):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n]


def _tok(n):
    return "%.1fk" % (n / 1000.0) if n >= 1000 else str(int(n))


def _summarize(name, inp):
    inp = inp if isinstance(inp, dict) else {}
    if name == "Read":
        s = str(inp.get("file_path", ""))
        for k in ("offset", "limit"):
            if k in inp:
                s += " %s=%s" % (k, inp[k])
    elif name == "Grep":
        s = 'pattern="%s"' % inp.get("pattern", "")
        if inp.get("path"):
            s += " path=%s" % inp["path"]
    elif name == "Glob":
        s = str(inp.get("pattern", ""))
    elif name == "Bash":
        s = str(inp.get("command", ""))
    else:
        s = json.dumps(inp, ensure_ascii=False, separators=(",", ":"))
    return _trunc(s)


def _content_text(content):
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return "" if content is None else str(content)


class StreamLogger:
    def __init__(self, label, out=sys.stdout, clock=time.monotonic,
                 now=lambda: time.strftime("%H:%M:%S")):
        self.label, self.out, self.clock, self.now = label, out, clock, now
        self.session_id = None
        self._t0 = self._mark = self._last = 0.0
        self._wall = None
        self._seen = {}      # message id -> True
        self._tools = {}     # tool_use id -> (n, name, clock)
        self._n_tools = 0
        self._s = {"turns": 0, "llm_s": 0.0, "tool_s": 0.0, "tool_calls": 0, "denied": 0,
                   "in_tokens": 0, "out_tokens": 0, "wall_s": 0.0, "result": None}

    def _p(self, msg):
        with _LOCK:
            print("%s [%s] %s" % (self.now(), self.label, msg), file=self.out, flush=True)

    def start(self, info):
        self._t0 = self._mark = self._last = self.clock()
        self._p("● start  %s" % info)

    def feed(self, line):
        line = line.rstrip("\r\n")
        if not line.strip():
            return
        try:
            ev = json.loads(line)
            if not isinstance(ev, dict):
                raise ValueError
        except ValueError:
            self._p("· %s" % line)
            return
        t = self._last = self.clock()
        if ev.get("session_id"):
            self.session_id = ev["session_id"]
        kind = ev.get("type")
        if kind == "assistant":
            self._assistant(ev.get("message") or {}, t)
        elif kind == "user":
            self._user(ev.get("message") or {}, t)
        elif kind == "result":
            self._result(ev, t)

    def _assistant(self, msg, t):
        mid = msg.get("id")
        if mid not in self._seen:
            self._seen[mid] = True
            u = msg.get("usage") or {}
            tin, tout = u.get("input_tokens", 0) or 0, u.get("output_tokens", 0) or 0
            lat = t - self._mark
            self._s["llm_s"] += lat
            self._s["in_tokens"] += tin
            self._s["out_tokens"] += tout
            self._s["turns"] += 1
            rate = "  (%d tok/s)" % round(tout / lat) if lat > 0 and tout else ""
            self._p("◆ LLM    %.1fs  in=%s out=%s%s" % (lat, _tok(tin), _tok(tout), rate))
        self._mark = t
        for b in msg.get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                self._n_tools += 1
                self._s["tool_calls"] += 1
                self._tools[b.get("id")] = (self._n_tools, b.get("name", "?"), t)
                self._p("▶ T%d %s  %s" % (self._n_tools, b.get("name", "?"),
                                          _summarize(b.get("name"), b.get("input"))))

    def _user(self, msg, t):
        content = msg.get("content")
        for b in content if isinstance(content, list) else []:
            if not isinstance(b, dict) or b.get("type") != "tool_result":
                continue
            n, name, t_use = self._tools.pop(b.get("tool_use_id"), ("?", "?", t))
            dt = t - t_use
            self._s["tool_s"] += dt
            text = _content_text(b.get("content"))
            if b.get("is_error"):
                guard = any(m in text for m in _GUARD_MARKERS)
                if guard:
                    self._s["denied"] += 1
                self._p("✖ T%s %s  %.1fs  %s: %s" % (n, name, dt, "denied" if guard else "error",
                                                    _trunc(text)))
            else:
                self._p("✔ T%s %s  %.1fs  %d lines" % (n, name, dt, len(text.splitlines())))
        self._mark = t

    def _result(self, ev, t):
        self._s["result"] = ev
        self._wall = t - self._t0
        if isinstance(ev.get("num_turns"), int):
            self._s["turns"] = ev["num_turns"]
        so = ev.get("structured_output")
        findings = len(so.get("findings") or []) if isinstance(so, dict) else 0
        api = (ev.get("duration_api_ms") or 0) / 1000.0
        self._p("■ done   %s  turns=%s  findings=%d  wall=%.1fs  api=%.1fs"
                % (ev.get("subtype", "?"), self._s["turns"], findings, self._wall, api))

    def finish(self, status, error=None):
        if self._s["result"] is not None:
            return
        self._wall = self.clock() - self._t0
        self._p("■ done   %s%s" % (status, "  %s" % error if error else ""))

    def stats(self):
        s = dict(self._s)
        s["wall_s"] = self._wall if self._wall is not None else self._last - self._t0
        return s
