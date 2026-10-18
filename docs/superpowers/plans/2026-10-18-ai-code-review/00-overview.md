# AI Code Review Kit Implementation Plan — Overview

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `ai-review` kit: a Python orchestrator plus reusable GitHub
Actions workflow that reviews PRs with `claude -p` backed by deepseek-v4-flash
via LiteLLM, on air-gapped self-hosted runners.

**Architecture:** `cli.py` computes the diff range (full or incremental), splits
it into chunks, runs up to 3 `claude -p` sessions in parallel (stream-json,
json-schema, max 10 turns, read-only tools guarded by a PreToolUse hook), logs
every event with timing, aggregates findings into a deterministic verdict, and
posts one PR review with a hidden state marker for the next run.

**Tech Stack:** Python 3.9+ standard library only, `unittest`, Claude Code
>= 2.1.302, GitHub Actions (GHES), LiteLLM, vLLM.

**Spec:** `docs/superpowers/specs/2026-10-18-ai-code-review-design.md`

## Plan Files

| File | Tasks |
|---|---|
| `00-overview.md` | this file: constraints, review focus, file map, shared shapes |
| `01-config-diff.md` | Task 1 config, Task 2 git helpers + diff splitting |
| `02-guard.md` | Task 3 guard hook + `settings.json` |
| `03-stream-prompt.md` | Task 4 stream logger, Task 5 agent/schema/prompt |
| `04-claude-runner.md` | Task 6 claude command/env, Task 7 runner |
| `05-aggregate-render.md` | Task 8 aggregate/verdict, Task 9 rendering |
| `06-github-cli.md` | Task 10 GitHub client, Task 11 cli + integration test |
| `07-workflow-smoke.md` | Task 12 workflow, Task 13 smoke fixture + runbook |

Execute in task order; each task lists what it consumes from earlier tasks.

## Global Constraints

- Python standard library only; no pip packages. Target Python 3.9.
- Each source and test file of this kit: ~150 lines. Split a module before it grows past ~180.
- Code, docs and code comments: English. Agent instructions, prompts, PR comments, review summary lines: Korean.
- Tests: `python3 -m unittest discover -s tests -v` must pass after every task.
- Run Python helper processes with `python3 -I` (isolated mode).
- Never pass `AI_REVIEW_BOT_PAT`, `GITHUB_TOKEN` or any `GH_*`/`GITHUB_*` token to the `claude` child process.
- Verdict rule: blocking = severity in `["critical","major"]` and confidence >= 80; drop confidence < 50.
- Limits: chunk 20000 est. tokens (chars/3.5), max 6 chunks, concurrency 3, max turns 10, explore budget 6,
  LLM request 180000 ms, Bash 60000 ms, chunk 480 s, fallback 120 s, whole run 1200 s, workflow 25 min.
- Commit after each task; message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Paths with spaces or non-ASCII names in the diff — expect correct file names (Task 2 test `test_quoted_and_unicode_paths`).
2. One generated/minified file whose single hunk exceeds the chunk limit — expect it truncated with a marker, never a crash or an oversized chunk (Task 2 test `test_oversized_single_hunk_truncated`).
3. PR whose changes are all deleted/binary/excluded — expect a COMMENT "리뷰 대상 변경 없음", not APPROVE (Task 8 test `test_no_chunks_is_comment`).
4. Model returns `./src/a.cu`, `a/src/a.cu`, or line 0/out-of-diff — expect path normalized and the finding moved to the body, never a 422 failure (Task 8 test `test_normalize_paths_and_lines`).
5. Previous state marker missing, corrupted, or bot login differing in case — expect a full review, no exception (Task 9 test `test_parse_state_corrupt`, Task 10 test `test_bot_login_case_insensitive`).

## File Map

```
config/defaults.json         Task 1   all tunables (keys below)
config/settings.json         Task 3   permissions + PreToolUse hook wiring
config/review-schema.json    Task 5   chunk output JSON schema
agent/pr-reviewer.md         Task 5   Korean reviewer instructions
review/__init__.py           Task 1   empty
review/config.py             Task 1   load_config
review/gitutil.py            Task 2   git subprocess helpers
review/diff_split.py         Task 2   parse diff, build chunks
review/hooks/guard.py        Task 3   PreToolUse enforcement script
review/stream_log.py         Task 4   StreamLogger
review/prompt.py             Task 5   build_prompt, FALLBACK_PROMPT
review/claude_cmd.py         Task 6   build_command, build_env, write_agents_file
review/claude_runner.py      Task 7   run_chunk, run_all, extract_output
review/aggregate.py          Task 8   merge, split_inline, finding_id
review/render.py             Task 9   render_body, render_inline, state marker, step summary
review/github_api.py         Task 10  GitHub client
review/cli.py                Task 11  entry point
.github/workflows/ai-review.yml  Task 12
examples/buggy-pr/           Task 13  smoke fixture
docs/runbook.md, docs/litellm-config.example.yaml  Task 13
tests/fakes/fake_claude.py   Task 7   scripted stream-json emitter
```

## Shared Data Shapes

All are plain dicts (JSON-serializable except where noted).

```python
Config = {  # config/defaults.json, all keys required
  "model": "deepseek-v4-flash", "claude_bin": "claude",
  "chunk_max_tokens": 20000, "max_chunks": 6, "concurrency": 3,
  "max_turns": 10, "explore_budget": 6,
  "llm_timeout_ms": 180000, "bash_timeout_ms": 60000,
  "chunk_timeout_s": 480, "fallback_timeout_s": 120, "deadline_s": 1200,
  "block_severities": ["critical", "major"], "block_confidence": 80, "min_confidence": 50,
  "exclude_globs": ["vendor/**", "third_party/**", "**/*.lock", "**/package-lock.json",
                    "**/*.min.js", "**/*.pb.go", "**/*_pb2.py"]}

FileDiff = {"path": str, "old_path": str | None, "status": "A|M|D|R",
            "binary": bool, "text": str,          # full per-file diff text
            "added_lines": list[int],             # new-side numbers of '+' lines
            "right_lines": list[int]}             # new-side numbers of '+' and ' ' lines

Chunk = {"index": int, "files": list[str], "diff": str, "est_tokens": int}

ChunkOutput = schema of config/review-schema.json (spec 7.3)

ChunkResult = {"index": int, "status": "ok|incomplete|failed",
               "output": ChunkOutput | None, "stats": Stats,
               "session_id": str | None, "error": str | None}

Stats = {"turns": int, "llm_s": float, "tool_s": float, "tool_calls": int,
         "denied": int, "in_tokens": int, "out_tokens": int, "wall_s": float,
         "result": dict | None}            # raw final "result" event

Finding = ChunkOutput finding + {"id": str, "blocking": bool, "needs_check": bool}

Review = {"event": "APPROVE|REQUEST_CHANGES|COMMENT",
          "findings": list[Finding], "previous": list[dict],   # previous findings with "status"
          "incomplete": list[int], "failed_all": bool, "no_targets": bool,
          "summaries": list[str], "dropped": int}

State = {"sha": str, "findings": [{"id","file","line","title","severity","blocking"}]}
```

Environment variables read by the kit:

| Name | Reader | Meaning |
|---|---|---|
| `AI_REVIEW_LLM_BASE_URL`, `AI_REVIEW_LLM_API_KEY`, `AI_REVIEW_MODEL` | claude_cmd | LiteLLM endpoint/key/model |
| `AI_REVIEW_BOT_PAT`, `AI_REVIEW_BOT_NAME` | cli → github_api | posting identity |
| `GITHUB_API_URL`, `GITHUB_REPOSITORY`, `GITHUB_STEP_SUMMARY` | cli | Actions context |
| `AI_REVIEW_REPO_ROOT`, `AI_REVIEW_STATE_DIR`, `AI_REVIEW_EXPLORE_BUDGET`, `AI_REVIEW_KIT` | guard (set by claude_cmd) | hook context |
