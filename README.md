# ai-review — AI pull-request review on air-gapped self-hosted runners

ai-review reviews GitHub Enterprise Server (GHES) pull requests with Claude Code (`claude -p`)
talking to an open-weight model (default `deepseek-v4-flash`). The model is served by vLLM
behind LiteLLM. Reviews run on self-hosted GitHub Actions runners, are posted by a bot account,
and are written in Korean.

- **Read-only reviewer.** The model can only read, search and run read-only `git` commands
  inside the PR checkout. No file edits, no network tools, no paths outside the repository.
- **Deterministic verdict.** The model only reports findings. Python code computes the result:
  - `REQUEST_CHANGES`: a critical/major finding with confidence ≥ 80.
  - `COMMENT`: the run could not review everything.
  - `APPROVE`: otherwise.
- **Content-based incremental review.** Later pushes review only lines not reviewed before.
  Squash, rebase, force-push and merging main cost no LLM call. Earlier findings follow their
  code and are re-checked.
- **Bounded cost.** The diff is split into chunks, with up to 3 `claude` sessions in parallel.
  Each session has a 10-turn limit and a tool budget, and the whole run must finish within
  about 20 minutes.
- **Visible process.** The Actions log shows every LLM request, tool call and timing. The PR
  gets one review with inline comments.

This README is the integration guide. Details live in:

| Document | Content |
|---|---|
| [`docs/runbook.md`](docs/runbook.md) | Operations: prerequisites, secrets, verdict rules, incremental behaviour, log symbols, troubleshooting, pending runner checks |
| [`docs/superpowers/specs/2026-10-18-ai-code-review-design.md`](docs/superpowers/specs/2026-10-18-ai-code-review-design.md) | Design spec (authoritative): security model, state marker, scope rules, verdict |
| [`docs/caller-example.yml`](docs/caller-example.yml) | Workflow to copy into each target repository |
| [`docs/litellm-config.example.yaml`](docs/litellm-config.example.yaml) | LiteLLM in front of vLLM |
| [`examples/buggy-pr/`](examples/buggy-pr/README.md) | Fixture PR with planted defects, used for the smoke test |

---

## 1. How it works

```
pull_request event (target repo)
  └─ caller workflow ──uses──▶ <org>/<kit-repo>/.github/workflows/ai-review.yml@<tag>
        self-hosted runner, bash (login shell may be tcsh)
        1. checkout PR head (fetch-depth 0, persist-credentials false, bot PAT)
        2. checkout this kit into .ai-review-kit
        3. python3 -E -s -m review --repo target --pr N
             ├─ github_api   read the previous bot review → state marker
             ├─ scope        base...head diff, keep hunks with not-yet-reviewed lines
             ├─ diff_split   prioritized chunks (≤ chunk_max_tokens, ≤ max_chunks)
             ├─ claude_runner  one `claude -p` per chunk (≤ concurrency in parallel)
             │     agent/pr-reviewer.md, config/settings.json, hooks/guard.py,
             │     --json-schema config/review-schema.json, stream-json → Actions log
             ├─ aggregate    merge, dedupe, relocate previous findings, verdict
             └─ github_api   post one review (+ state marker); dismiss the old
                             REQUEST_CHANGES only when the new verdict is APPROVE
        4. upload stream.jsonl, guard-audit.jsonl, review.json as artifacts
```

Only the Python parent process holds the bot PAT. The `claude` child gets the following
environment, built from scratch:
- an empty temporary `CLAUDE_CONFIG_DIR`
- the LiteLLM URL and key as `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN`
- the model name in every model variable
- `PATH`, `HOME` and the locale

It never receives a GitHub token.

## 2. Repository layout

```
.github/workflows/ai-review.yml  reusable workflow (workflow_call)
agent/pr-reviewer.md             reviewer instructions and rubric (Korean)
config/defaults.json             thresholds, limits, timeouts, exclude globs
config/review-schema.json        JSON schema of one chunk's output
config/settings.json             Claude Code permissions + PreToolUse guard hook
review/                          Python stdlib-only orchestrator (entry: python3 -m review)
  cli.py, cli_util.py            entry point, flow, deadline
  scope.py, hunks.py, fingerprint.py, seen.py, previous.py, state_fit.py
                                 content-based incremental scope and state marker
  diff_split.py                  diff parsing, chunking, priorities
  claude_cmd.py, claude_runner.py, stream_log.py
                                 claude command/env, timeouts, resume fallback, live log
  prompt.py, aggregate.py, validate.py, render.py, failure.py
                                 prompt, verdict, output validation, PR text
  github_api.py, gitutil.py      GHES REST client, git helpers
  hooks/guard.py                 path boundary, git-only Bash, exploration budget, audit log
tests/                           unittest suite with fake `claude` and fake GitHub
examples/buggy-pr/               smoke-test fixture
```

## 3. Requirements

**Runner** (each self-hosted runner that can pick up the job)
- Claude Code ≥ 2.1.302, installed offline and on `PATH` for a **non-login bash**. Set `PATH` in
  the runner service environment (systemd unit or runner `.env`), not only in tcsh rc files.
- Python ≥ 3.9 and git. The kit has no pip dependencies.
- Network: GHES and the LiteLLM endpoint only. No SSH needed. Everything goes over HTTPS with the PAT.

**GHES**
- `actions/checkout@v4` and `actions/upload-artifact@v3.2.2-node20` must be available. On an
  air-gapped instance, sync them with actions-sync. `upload-artifact@v4` is not supported on GHES.
- A **bot account** with write access to the target repositories, and a PAT for it with the
  `repo` scope (classic), or contents read and pull-requests write (fine-grained).

**LLM**
- Any endpoint that speaks the Anthropic Messages API. Normally LiteLLM in front of vLLM; see
  `docs/litellm-config.example.yaml`.
- The model must handle tool use and JSON-schema structured output.
- Context should be ≥ ~128k tokens. Otherwise lower `chunk_max_tokens`.

## 4. Installation

1. **Host the kit.** Push this repository to a GHES organization, for example `<org>/ai-review`,
   and create the release tag the callers will pin:
   ```bash
   git tag v1 && git push origin v1
   ```
   The reusable workflow checks itself out again via the `kit_repository` / `kit_ref` inputs
   (defaults `your-org/ai-review` and `v1`). Either change those defaults in
   `.github/workflows/ai-review.yml` to your names, or pass them from every caller.
2. **Create organization secrets.** Give the target repositories access to them.

   | Secret | Value |
   |---|---|
   | `AI_REVIEW_BOT_PAT` | PAT of the bot account (reviews are posted as this identity) |
   | `AI_REVIEW_BOT_NAME` | login of the bot account (used to find its earlier reviews) |
   | `AI_REVIEW_LLM_API_KEY` | LiteLLM key |
   | `AI_REVIEW_LLM_BASE_URL` | LiteLLM base URL, e.g. `https://litellm.internal:4000` |
3. **Add the caller workflow** to each target repository as `.github/workflows/ai-review.yml`:
   ```yaml
   name: ai-review
   on:
     pull_request:
       types: [opened, synchronize, reopened]
   jobs:
     review:
       permissions:
         contents: read          # posting uses the bot PAT, not GITHUB_TOKEN
       uses: <org>/ai-review/.github/workflows/ai-review.yml@v1
       secrets: inherit
       with:
         kit_repository: <org>/ai-review   # if you did not change the default
         runner_labels: '["self-hosted", "linux"]'
         # model: deepseek-v4-flash
         # max_chunks: 6
         # concurrency: 3
   ```
   The caller must grant at least `contents: read`. A called workflow cannot raise the caller's
   `GITHUB_TOKEN` permissions, and the kit never asks for more.
4. **Branch protection.** Require a human approval. The bot's approval is advisory, because
   prompt injection inside a PR can at worst make the model under-report.
5. **Verify** (section 8) before turning it on for many repositories.

## 5. Replacing an existing AI review

Steps for migrating a repository from another review bot or workflow:

1. **Disable the old workflow** in the same PR that adds the caller workflow, so two bots never
   review the same PR.
2. **Use a separate bot account, or reuse the old one.** The kit only reads reviews whose author
   is `AI_REVIEW_BOT_NAME` and whose body has a valid `<!-- ai-review-state {"v": 2 ...} -->`
   marker. Other reviews, including the old bot's, are ignored. So the first run on an open PR
   is a full review.
3. **Old blocking reviews.** The kit dismisses only its own `REQUEST_CHANGES` reviews. Dismiss the
   old bot's change requests by hand, or let authors re-request review.
4. **Rules and language.**
   - Reviewer behaviour lives in `agent/pr-reviewer.md` and `config/defaults.json`, not in the
     target repository. PR authors cannot change it.
   - Port house rules from the old system there: focus areas, severity rubric, exclude globs.
   - The PR text is Korean. User-facing strings live in `review/render.py`,
     `review/aggregate.py` and `agent/pr-reviewer.md`.
5. **Different LLM backend.** Point `AI_REVIEW_LLM_BASE_URL` at any Anthropic-compatible endpoint.
   For OpenAI-compatible servers, put LiteLLM in between. Set `model` in the caller. Then re-check
   section 8, items 5–7.

## 6. Configuration

All keys are in `config/defaults.json`. Unknown keys are rejected. Override them in one of two
ways:
- with `--set key=<json>` on the CLI;
- with the workflow inputs `model`, `max_chunks` and `concurrency`.

| Key | Default | Meaning |
|---|---|---|
| `model` | `deepseek-v4-flash` | model name sent to LiteLLM |
| `chunk_max_tokens` | 20000 | chunk size (estimated as characters / 3.5) |
| `max_chunks` | 6 | chunks per run; the rest are skipped, and the next run resumes them |
| `concurrency` | 3 | parallel `claude` sessions |
| `max_turns` | 10 | turns per session; the last turns must produce the result |
| `explore_budget` | 6 | Read/Grep/Glob/Bash calls per session before the guard forces the answer |
| `llm_timeout_ms` / `bash_timeout_ms` | 180000 / 60000 | per request / per git command |
| `chunk_timeout_s` / `fallback_timeout_s` | 480 / 120 | per chunk / resume fallback |
| `deadline_s` | 1200 | whole run (20 min) |
| `block_severities`, `block_confidence` | critical, major; 80 | what blocks |
| `min_confidence` | 50 | findings below this are dropped |
| `exclude_globs` | vendor, third_party, lock files, generated code | never reviewed |

## 7. Interfaces

These are useful when wiring the kit into other automation.

**CLI** (run from the kit directory):
```
python3 -E -s -m review --repo <checkout> --pr <n>                     # online: read state, post
python3 -E -s -m review --dry-run --repo <checkout> --base <sha> --head <sha> \
        [--state-file prev.json] [--out-dir dir]                       # local: post nothing
        [--config extra.json] [--set key=<json> ...]
```
- **Environment, both modes:** `AI_REVIEW_LLM_BASE_URL`, `AI_REVIEW_LLM_API_KEY`, and
  optionally `AI_REVIEW_MODEL`.
- **Environment, online mode only:** `GITHUB_API_URL`, `GITHUB_REPOSITORY`,
  `AI_REVIEW_BOT_PAT` and `AI_REVIEW_BOT_NAME`.
- **Exit codes:**
  - `0`: a review was posted or printed, or the run was skipped because there were no new lines.
  - `1`: one of these failures. A failure comment is posted when possible.
    - reading from GitHub or git failed;
    - posting the review, or dismissing the old change request, failed;
    - the LLM failed for every chunk.
  - `2`: invalid configuration or missing environment.

**Outputs**
- `<out-dir>/review.json`: `{"event", "body", "comments"}`, the exact payload that was posted.
- `<out-dir>/<chunk>/stream.jsonl`: raw Claude stream events.
- `<out-dir>/<chunk>/guard-audit.jsonl`: every guard decision.
- `$GITHUB_STEP_SUMMARY`: a per-chunk table.

**Chunk output** (`config/review-schema.json`, enforced with `--json-schema`):
- `summary`
- `findings[]`, each `{file, line, end_line?, severity, confidence, category, title, detail,
  suggestion?}`
- `previous_findings[]`, each `{id, status: resolved|open|unclear, note?}`
- `incomplete_reason`

**State marker.** The kit's state lives in an HTML comment inside the bot review body:
```
<!-- ai-review-state {"v": 2, "sha", "complete", "seen", "seen_files", "seen_hunks", "findings"} -->
```
It holds line fingerprints of what was reviewed, plus the open findings with content anchors.
Its size is capped at 40,000 characters. Do not edit or strip it. Spec section 6.2 defines it.

## 8. Verification

1. **Unit and integration tests.** These use a fake `claude` and a fake GitHub, so no network
   is needed. The run takes about 13 minutes.
   ```bash
   python3 -m unittest discover -s tests
   ```
2. **Smoke test on a runner** with the real model:
   ```bash
   examples/buggy-pr/make_repo.sh /tmp/x
   cd <kit> && python3 -E -s -m review --dry-run --repo /tmp/x --base main --head feature
   ```
   It passes when all of the following hold:
   - at least 3 of the 4 planted defects are reported;
   - `review.json` has the event `REQUEST_CHANGES`;
   - every chunk logs `■ done`;
   - no `✖ ... denied` line comes from a real escape attempt.
3. **Runner checklist.** Work through the "Pending verification on the runner" table in
   `docs/runbook.md`. It covers:
   - that a PR's `CLAUDE.md` is not loaded;
   - the guard audit log;
   - `--resume`;
   - fixed prompt tokens;
   - `max_model_len`;
   - that `--agent` replaces the system prompt.

   **None of these has been run yet** on a real runner and model. They need the air-gapped
   runner with LiteLLM/vLLM.

## 9. Notes for AI agents doing the integration

When an AI agent installs this kit or migrates a repository to it, follow sections 4–5 in order.
Do not change these invariants; each one is load-bearing for security or for correctness of the
verdict:

1. The `claude` child process gets **no GitHub token**. Keep `persist-credentials: false` on both
   checkouts. Only `review/github_api.py` uses `AI_REVIEW_BOT_PAT`.
2. Keep the tool limits in `config/settings.json` and `review/hooks/guard.py` as they are.
   - Tools: `Read`/`Grep`/`Glob` are allowed inside the repository only, and `Bash` only for
     read-only `git` subcommands.
   - Shell metacharacters are denied.
   - Mode stays `dontAsk`, and `CLAUDE_CONFIG_DIR` stays a fresh empty directory.
3. The verdict is computed in `review/aggregate.py`, never by the model. Never let a run that
   did not review everything return `APPROVE`. That covers partial, failed or truncated chunks,
   skipped files, and an unconfirmed previous blocker.
4. The state marker format (v2) is shared between runs. Changing it means the next run on every
   open PR does a full review again. Bump `"v"` if you change its meaning.
5. Reusable workflow permissions stay `contents: read`. Posting uses the bot PAT.
6. Run `python3 -m unittest discover -s tests` after every change. Kit modules are kept to about
   150–180 lines each.

Things that are **not** provided:
- There is no manual "full re-review" trigger.
- An issue missed in an earlier review is not raised again unless its lines change.
- Merging is not gated by the bot alone. Use branch protection with a human approval.
