# Plan Part 4 — Claude Command and Runner

Read `00-overview.md` first. Spec sections 4.1, 4.5, 5.

### Task 6: Claude command line and child environment

**Files:**
- Create: `review/claude_cmd.py`
- Test: `tests/test_claude_cmd.py`

**Interfaces:**
- Consumes: `Config` (Task 1), `KIT_DIR` (Task 1), `agent/pr-reviewer.md` (Task 5),
  `config/settings.json` (Task 3), `config/review-schema.json` (Task 5).
- Produces:
  - `load_agent(md_path: str) -> dict` — `{"pr-reviewer": {"description": str, "prompt": str}}`
    (frontmatter `name`/`description`, body = prompt).
  - `write_agents_file(md_path: str, out_dir: str) -> str` — writes the JSON above to `<out_dir>/agents.json`.
  - `build_command(cfg: dict, agents_file: str, resume_session: str | None = None) -> list[str]`
  - `build_env(cfg: dict, repo_root: str, state_dir: str, config_dir: str, base_env: dict) -> dict`
  - `ENV_PASSTHROUGH = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "USER")`

`build_command` normal run, in this order:

```text
<claude_bin> -p --verbose --output-format stream-json
  --agent pr-reviewer --agents <agents_file>
  --json-schema <contents of config/review-schema.json, compact>
  --max-turns <max_turns> --tools Read,Grep,Glob,Bash
  --permission-mode dontAsk --setting-sources user
  --settings <KIT_DIR>/config/settings.json --strict-mcp-config
```

With `resume_session`: same, but `--resume <id>`, `--tools ""`, `--max-turns 2`.

`build_env` sets exactly: `ENV_PASSTHROUGH` keys present in `base_env`, plus
`CLAUDE_CONFIG_DIR=config_dir`, `ANTHROPIC_BASE_URL=$AI_REVIEW_LLM_BASE_URL`,
`ANTHROPIC_AUTH_TOKEN=$AI_REVIEW_LLM_API_KEY`, `ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_HAIKU_MODEL`,
`ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_OPUS_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL` (all = `cfg["model"]`,
or `$AI_REVIEW_MODEL` when set), `API_TIMEOUT_MS`, `BASH_DEFAULT_TIMEOUT_MS`, `BASH_MAX_TIMEOUT_MS`,
`DISABLE_AUTOUPDATER=1`, `DISABLE_TELEMETRY=1`, `DISABLE_ERROR_REPORTING=1`,
`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, `AI_REVIEW_REPO_ROOT`, `AI_REVIEW_STATE_DIR`,
`AI_REVIEW_EXPLORE_BUDGET`, `AI_REVIEW_KIT=KIT_DIR`. Missing LLM URL or key → `ValueError`.
The retry-limit variable name is confirmed in Task 13 (spec 4.5 item 2); add it here then.

- [ ] **Step 1: Write `tests/test_claude_cmd.py`**

```python
def test_load_agent(self):
    a = load_agent(os.path.join(KIT_DIR, "agent/pr-reviewer.md"))["pr-reviewer"]
    self.assertTrue(a["description"]); self.assertIn("## 역할", a["prompt"])
    self.assertFalse(a["prompt"].startswith("---"))

def test_command_normal(self):
    c = build_command(CFG, "/t/agents.json")
    self.assertEqual(c[:5], ["claude", "-p", "--verbose", "--output-format", "stream-json"])
    self.assertEqual(c[c.index("--max-turns") + 1], "10")
    self.assertEqual(c[c.index("--tools") + 1], "Read,Grep,Glob,Bash")
    self.assertEqual(c[c.index("--permission-mode") + 1], "dontAsk")
    json.loads(c[c.index("--json-schema") + 1])
    self.assertNotIn("--resume", c)

def test_command_resume(self):
    c = build_command(CFG, "/t/agents.json", resume_session="abc")
    self.assertEqual(c[c.index("--resume") + 1], "abc")
    self.assertEqual(c[c.index("--tools") + 1], "")
    self.assertEqual(c[c.index("--max-turns") + 1], "2")

def test_env_has_no_github_tokens(self):
    base = {"PATH": "/bin", "HOME": "/h", "AI_REVIEW_BOT_PAT": "x", "GITHUB_TOKEN": "y",
            "GH_TOKEN": "z", "AI_REVIEW_LLM_BASE_URL": "http://llm:4000", "AI_REVIEW_LLM_API_KEY": "k"}
    e = build_env(CFG, "/repo", "/state", "/cfg", base)
    for bad in ["AI_REVIEW_BOT_PAT", "GITHUB_TOKEN", "GH_TOKEN"]:
        self.assertNotIn(bad, e)
    self.assertNotIn("x", e.values()); self.assertNotIn("y", e.values())
    self.assertEqual(e["ANTHROPIC_BASE_URL"], "http://llm:4000")
    self.assertEqual(e["ANTHROPIC_MODEL"], "deepseek-v4-flash")
    self.assertEqual(e["API_TIMEOUT_MS"], "180000")
    self.assertEqual(e["AI_REVIEW_EXPLORE_BUDGET"], "6")

def test_env_requires_llm(self):
    with self.assertRaises(ValueError):
        build_env(CFG, "/repo", "/s", "/c", {"PATH": "/bin"})
```

- [ ] **Step 2: Run** `python3 -m unittest tests.test_claude_cmd -v` — Expected: FAIL.
- [ ] **Step 3: Implement `review/claude_cmd.py`.**
- [ ] **Step 4: Run** `python3 -m unittest discover -s tests -v` — Expected: all OK.
- [ ] **Step 5: Commit** `feat: build claude command line and isolated env`

---

### Task 7: Chunk runner with timeouts and conclusion fallback

**Files:**
- Create: `review/claude_runner.py`, `tests/fakes/fake_claude.py`
- Test: `tests/test_claude_runner.py`

**Interfaces:**
- Consumes: `build_command`, `build_env` (Task 6), `StreamLogger` (Task 4), `FALLBACK_PROMPT` (Task 5).
- Produces:
  - `extract_output(result: dict | None) -> dict | None` — `result["structured_output"]` if a dict, else the
    first parseable `{...}` JSON object found in `result["result"]` text that has a `findings` key, else None.
  - `run_process(cmd: list[str], env: dict, cwd: str, stdin_text: str, timeout_s: float, logger) -> str`
    — returns `"exited"` or `"timeout"`; streams stdout lines to `logger.feed`; kills the process group on timeout.
  - `run_chunk(chunk: dict, prompt: str, total: int, cfg: dict, paths: dict, base_env: dict) -> ChunkResult`
    where `paths = {"repo": str, "work": str, "agents_file": str}`; per chunk it creates
    `<work>/chunk-<i>/{state,config}` and tees raw stdout to `<work>/chunk-<i>/stream.jsonl`.
  - `run_all(chunks, prompts, cfg, paths, base_env, deadline: float) -> list[ChunkResult]`
    — `ThreadPoolExecutor(cfg["concurrency"])`; a chunk not started before `deadline` (monotonic) returns
    `status="incomplete", error="deadline"`; per-chunk timeout = `min(chunk_timeout_s, deadline - now)`.

`run_chunk` outcome rules:

| Situation | Action | Status |
|---|---|---|
| exited, `extract_output` ok | — | `ok` |
| exited without output, or `error_max_turns`, session id known | resume once with `FALLBACK_PROMPT`, `fallback_timeout_s` | `ok` if output else `incomplete` |
| timeout | kill; resume once if session id known and time remains | as above, `error="timeout"` |
| no session id, non-zero exit | — | `failed`, `error` = last 500 chars of stderr |

`output["incomplete_reason"]` non-null keeps status `ok`; aggregate reports it.

- [ ] **Step 1: Write `tests/fakes/fake_claude.py`** (executable, `#!/usr/bin/env python3`): reads scenario from
  env `FAKE_CLAUDE_SCENARIO` (`ok`, `max_turns`, `hang`, `broken`, `text_json`, `crash`); when `--resume` is in argv
  it emits a success result with structured_output; records argv and stdin to `$FAKE_CLAUDE_LOG` (JSONL).
  `hang` prints init then sleeps 60 s.
- [ ] **Step 2: Write `tests/test_claude_runner.py`** (cfg with `claude_bin` = fake path,
  `chunk_timeout_s=2`, `fallback_timeout_s=2`):

```python
def test_ok(self):            r = self.run_scn("ok");        self.assertEqual(r["status"], "ok")
def test_max_turns_resumes(self):
    r = self.run_scn("max_turns")
    self.assertEqual(r["status"], "ok")
    self.assertIn("--resume", self.calls()[1]["argv"])
def test_hang_times_out_then_resumes(self):
    t = time.monotonic(); r = self.run_scn("hang")
    self.assertLess(time.monotonic() - t, 6); self.assertEqual(r["error"], "timeout")
def test_broken_json_incomplete(self): ...   # resume also broken (FAKE_RESUME=broken) -> "incomplete"
def test_text_json_extracted(self): ...       # structured_output missing, JSON inside result text -> "ok"
def test_crash_failed(self):  r = self.run_scn("crash");     self.assertEqual(r["status"], "failed")
def test_prompt_on_stdin(self): ...           # recorded stdin == prompt
def test_run_all_deadline(self):
    rs = run_all(chunks_x4, prompts, dict(cfg, concurrency=1), paths, env, deadline=time.monotonic() + 0.5)
    self.assertIn("deadline", [r["error"] for r in rs])
    self.assertEqual([r["index"] for r in rs], [0, 1, 2, 3])        # ordered by index
```

- [ ] **Step 3: Run** `python3 -m unittest tests.test_claude_runner -v` — Expected: FAIL.
- [ ] **Step 4: Implement `review/claude_runner.py`.** `run_process`: `Popen(..., stdin=PIPE, stdout=PIPE,
  stderr=PIPE, text=True, start_new_session=True)`; write stdin then close; reader threads for stdout
  (→ tee file + `logger.feed`) and stderr (kept, last 500 chars); `proc.wait(timeout)`; on
  `TimeoutExpired` `os.killpg(SIGKILL)`. Logger label `c<index+1>`; `start()` info
  `chunk i/total  files=N  diff=X.Xk tok`.
- [ ] **Step 5: Run** `python3 -m unittest discover -s tests -v` — Expected: all OK, runner tests < 30 s.
- [ ] **Step 6: Commit** `feat: add chunk runner with timeouts and fallback`
