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
  Each session has a 30-turn limit and a tool budget, and the whole run must finish within
  110 minutes (job limit 2 hours). Sized so a 2,000-line PR is reviewed with room to spare
  and a 20,000-line PR still fits.
- **Visible process.** The Actions log shows every LLM request, tool call and timing. The PR
  gets one review with inline comments.

This README is the integration guide. Details live in:

| Document | Content |
|---|---|
| [`docs/runbook.md`](docs/runbook.md) | Operations: prerequisites, secrets, verdict rules, incremental behaviour, log symbols, troubleshooting, pending runner checks |
| [`docs/superpowers/specs/2026-10-18-ai-code-review-design.md`](docs/superpowers/specs/2026-10-18-ai-code-review-design.md) | Design spec (authoritative): security model, state marker, scope rules, verdict |
| [`install.sh`](install.sh) | Installs or updates the kit in a target repository |
| [`templates/ai-review.yml`](templates/ai-review.yml) | Workflow that `install.sh` puts into each target repository |
| [`docs/litellm-config.example.yaml`](docs/litellm-config.example.yaml) | LiteLLM in front of vLLM |
| [`examples/buggy-pr/`](examples/buggy-pr/README.md) | Fixture PR with planted defects, used for the smoke test |

---

## 1. How it works

The kit is not run from a central repository. `install.sh` copies it into each target
repository as `.github/ai-review/`, next to the workflow `.github/workflows/ai-review.yml`.
Each PR runs the workflow and the kit from its own checkout.

```
pull_request event (target repo)
  └─ .github/workflows/ai-review.yml (from the PR head)
        self-hosted runner, bash (login shell may be tcsh)
        1. checkout PR head (fetch-depth 0, persist-credentials false, bot PAT)
        2. cd .github/ai-review
           python3 -E -s -m review --repo "$GITHUB_WORKSPACE" --pr N
             ├─ github_api   read the previous bot review → state marker
             ├─ scope        base...head diff, keep hunks with not-yet-reviewed lines
             ├─ diff_split   prioritized chunks (≤ chunk_max_tokens, ≤ max_chunks);
             │               files under .github/ai-review/ are never reviewed
             ├─ claude_runner  one `claude -p` per chunk (≤ concurrency in parallel)
             │     agent/pr-reviewer.md, config/settings.json, hooks/guard.py,
             │     --json-schema config/review-schema.json, stream-json → Actions log
             ├─ aggregate    merge, dedupe, relocate previous findings, verdict
             └─ github_api   post one review (+ state marker); dismiss the old
                             REQUEST_CHANGES only when the new verdict is APPROVE
        3. upload stream.jsonl, guard-audit.jsonl, review.json as artifacts
```

Only the Python parent process holds the bot PAT. The `claude` child gets the following
environment, built from scratch:
- an empty temporary `CLAUDE_CONFIG_DIR`
- the LiteLLM URL and key as `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN`
- the model name in every model variable
- `PATH`, `HOME` and the locale

It never receives a GitHub token.

**Trust model (accepted trade-off).** The workflow runs the PR's own copy of the workflow
and of the kit. A PR author can therefore change the review rules, the prompt or the guard
in that PR, or change the workflow to read the bot PAT. This is accepted for a trusted team.
Branch protection with a required human approval is still recommended, and the bot's verdict
stays advisory.

## 2. Repository layout

```
install.sh                       installs/updates the kit in a target repository
templates/ai-review.yml          workflow installed as .github/workflows/ai-review.yml
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
tests/                           unittest suite with fake `claude` and fake GitHub (not installed)
examples/buggy-pr/               smoke-test fixture
```

In a target repository, after `install.sh`:

```
.github/workflows/ai-review.yml  the workflow (from templates/; yours to edit)
.github/ai-review/               installed kit: review/, agent/, config/,
                                 README.md, VERSION (kit git sha), .gitignore
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
- The four secrets of section 4, at repository or organization level.

**Workstation** (where you run `install.sh`)
- bash, git, and a clone of this kit repository plus a clone of the target repository.

**LLM**
- Any endpoint that speaks the Anthropic Messages API. Normally LiteLLM in front of vLLM; see
  `docs/litellm-config.example.yaml`.
- The model must handle tool use and JSON-schema structured output.
- Context should be ≥ ~128k tokens. Otherwise lower `chunk_max_tokens`.

## 4. Installation

1. **Install the kit into the target repository** from a clone of this repository:
   ```bash
   ./install.sh /path/to/target-repo
   ```
   - It replaces `<target>/.github/ai-review/` with `review/`, `agent/` and `config/` from this
     kit, plus a short `README.md`, a `VERSION` file (the kit's git sha) and a `.gitignore`.
   - It writes `<target>/.github/workflows/ai-review.yml` from `templates/ai-review.yml` only if
     that file does not exist yet. Pass `--force-workflow` to overwrite it.
   - It touches nothing else in the target.
2. **Check the workflow.** At the top of `.github/workflows/ai-review.yml`, edit `runs-on`
   (default `[self-hosted]`) and, if needed, the `env:` values `AI_REVIEW_MODEL`, `MAX_CHUNKS`
   and `CONCURRENCY`.
3. **Create the secrets**, at repository level or as organization secrets the repository can use:

   | Secret | Value |
   |---|---|
   | `AI_REVIEW_BOT_PAT` | PAT of the bot account (reviews are posted as this identity) |
   | `AI_REVIEW_BOT_NAME` | login of the bot account (used to find its earlier reviews) |
   | `AI_REVIEW_LLM_API_KEY` | LiteLLM key |
   | `AI_REVIEW_LLM_BASE_URL` | LiteLLM base URL, e.g. `https://litellm.internal:4000` |
4. **Commit and open a PR:**
   ```bash
   cd /path/to/target-repo
   git add .github/ai-review .github/workflows/ai-review.yml
   git commit -m "ci: install AI review kit"
   ```
   The PR that adds the kit is already reviewed by it. The files under `.github/ai-review/` are
   excluded from review (`exclude_globs`); the workflow file is reviewed like any other file.
5. **Branch protection.** Require a human approval. The bot's approval is advisory: prompt
   injection inside a PR can make the model under-report, and a PR author can change the
   workflow or the kit in their PR (section 1, trust model).
6. **Verify** (section 8) before turning it on for many repositories.

**Updating.** Pull this kit repository, re-run `./install.sh /path/to/target-repo`, review the
diff under `.github/ai-review/` and commit it. Your workflow file is kept; compare it with
`templates/ai-review.yml` and pass `--force-workflow` if you want the new template.

**Local changes.** Do not edit `.github/ai-review/` by hand: the next `install.sh` run replaces
it. Change the kit repository instead (or keep a fork of it per team) and re-install.

## 5. Replacing an existing AI review

Steps for migrating a repository from another review bot or workflow:

1. **Disable the old workflow** in the same PR that installs the kit, so two bots never
   review the same PR.
2. **Use a separate bot account, or reuse the old one.** The kit only reads reviews whose author
   is `AI_REVIEW_BOT_NAME` and whose body has a valid `<!-- ai-review-state {"v": 2 ...} -->`
   marker. Other reviews, including the old bot's, are ignored. So the first run on an open PR
   is a full review.
3. **Old blocking reviews.** The kit dismisses only its own `REQUEST_CHANGES` reviews. Dismiss the
   old bot's change requests by hand, or let authors re-request review.
4. **Rules and language.**
   - Reviewer behaviour lives in `agent/pr-reviewer.md` and `config/defaults.json` of the kit
     repository, and is copied into each target as `.github/ai-review/`.
   - Port house rules from the old system there and re-run `install.sh`: focus areas, severity
     rubric, exclude globs.
   - Because each PR runs its own copy, a PR author can change these rules in their PR. Watch
     for diffs under `.github/ai-review/` in human review (they are not AI-reviewed), or use
     CODEOWNERS on that path.
   - The PR text is Korean. User-facing strings live in `review/render.py`,
     `review/aggregate.py` and `agent/pr-reviewer.md`.
5. **Different LLM backend.** Point `AI_REVIEW_LLM_BASE_URL` at any Anthropic-compatible endpoint.
   For OpenAI-compatible servers, put LiteLLM in between. Set `AI_REVIEW_MODEL` in the workflow's
   `env:`. Then re-check section 8, items 5–7.

## 6. Configuration

All keys are in `config/defaults.json`. Unknown keys are rejected. Override them in one of two
ways:
- with `--set key=<json>` on the CLI;
- with the workflow `env:` values `AI_REVIEW_MODEL`, `MAX_CHUNKS` and `CONCURRENCY` (passed as
  the model and `--set max_chunks=... --set concurrency=...`).

| Key | Default | Meaning |
|---|---|---|
| `model` | `deepseek-v4-flash` | model name sent to LiteLLM |
| `chunk_max_tokens` | 20000 | chunk size (estimated as characters / 3.5) |
| `max_chunks` | 30 | ceiling on chunks per run, so a large PR is not cut off; in practice `deadline_s` limits how many run, and chunks past it stay unreviewed for the next run |
| `concurrency` | 3 | parallel `claude` sessions |
| `max_turns` | 30 | turns per session; kept at least 10 above `explore_budget` so the answer still fits |
| `explore_budget` | 20 | Read/Grep/Glob/Bash calls per session before the guard forces the answer |
| `llm_timeout_ms` / `bash_timeout_ms` | 300000 / 60000 | per request / per git command |
| `chunk_timeout_s` / `fallback_timeout_s` | 1200 / 300 | per chunk / resume fallback |
| `deadline_s` | 6600 | whole run (110 min; the job's `timeout-minutes` is 120, leaving 10 min for checkout and posting). Raise both together |
| `block_severities`, `block_confidence` | critical, major; 80 | what blocks |
| `min_confidence` | 50 | findings below this are dropped |
| `exclude_globs` | vendor, third_party, lock files, generated code, `.github/ai-review/**` | never reviewed |

## 7. Interfaces

These are useful when wiring the kit into other automation.

**CLI** (run from the kit directory: this repository, or `.github/ai-review/` in a target):
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

When an AI agent installs this kit or migrates a repository to it, follow sections 4–5 in order:
run `install.sh <target>`, adjust `runs-on` and the `env:` values in the installed workflow,
make sure the four secrets exist, and commit `.github/ai-review/` plus the workflow in one PR.
Do not hand-copy files instead of running `install.sh`, and do not edit `.github/ai-review/`
in the target; change the kit repository and re-install.

Do not change these invariants; each one is load-bearing for security or for correctness of the
verdict:

1. The `claude` child process gets **no GitHub token**. Keep `persist-credentials: false` on the
   checkout. Only `review/github_api.py` uses `AI_REVIEW_BOT_PAT`.
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
5. Workflow permissions stay `contents: read`. Posting uses the bot PAT. Keep the plain
   `pull_request` trigger; do not switch to `pull_request_target`, which would run with
   secrets on PRs from forks.
6. Keep `.github/ai-review/**` in `exclude_globs`, so the installed kit is not reviewed.
7. The trust model is deliberate: the PR's own workflow and kit run, so PR authors are trusted
   (section 1). Do not present the bot as a gate that PR authors cannot bypass.
8. Run `python3 -m unittest discover -s tests` after every change. Kit modules are kept to about
   150–180 lines each.

Things that are **not** provided:
- There is no manual "full re-review" trigger.
- An issue missed in an earlier review is not raised again unless its lines change.
- Merging is not gated by the bot alone. Use branch protection with a human approval.
