# Mention Feedback — Design

- Date: 2026-11-11
- Status: Draft (awaiting review), rev 2
- Extends `2026-10-18-ai-code-review-design.md` (base design) and
  `2026-11-02-review-convergence-design.md` (issue ledger, state marker v3).

## History

- Rev 1: the model's `quote` from the comment authorized a withdrawal or a guideline; ledger
  actions were journaled in bot replies and replayed with a per-kind watermark `mw`; the
  mention job built its review through `scope.determine` and `merge`.
- Rev 2, after a Codex adversarial review: a quote proves nothing (`quote: "@ai-bot"` passes),
  so loosening actions need an explicit command parsed by code (section 5). Replies sorted by
  kind could apply a withdrawal before its issue existed, a mention could overwrite a
  finished push review, `merge` with no results closed unrelated orphans, and a retry dropped
  an already applied withdrawal. Rev 2 replaces the reply journal with mention deltas in
  review markers, ordered by review id, a canonical ledger rule, a post-check with a sync
  review, and scoped dismissals (section 6). The mention job no longer calls `merge`. New
  issues carry the sha their line refers to. Also: `side` of inline comments, deadline
  propagation, semantic guideline retry, footer-only reply markers, POST retries.

## 1. Goal

A person mentions the bot (`@<bot name>`) in a pull request and the bot responds to that
comment in its context. In order of priority:

1. **Conversation (B).** The bot answers the question or objection.
2. **Verdict change (A).** A withdrawal the person commands and the bot agrees with, or a
   defect the bot confirms, changes the ledger at once, and a review with the updated
   verdict is posted in the same job.
3. **Guidelines (C).** A guideline the person commands is committed by the bot to a file in
   the PR branch; later reviews read the guidelines that apply to their files.

Success criteria:

- A mention gets one reply within the job limit (30 min), normally a few minutes.
- Ledger changes from mentions and from push reviews are never lost, whatever the order in
  which the jobs read and post (section 6).
- The model only reads. Replies, reviews and commits are made by the orchestrator.
- Text planted in PR code or produced by the model cannot cause a withdrawal or a guideline
  change: those need a command in a person's comment, parsed by code.
- Every guideline file is at most 100 lines, in a fixed directory layout (section 7).

Constraints are those of the base design: air-gapped GHES, LiteLLM, Python standard library,
kit code and docs in English, everything shown to people in Korean, trusted team.

## 2. Decisions

| Topic | Decision |
|---|---|
| Where the bot can be called | A general PR comment, a new inline comment, a reply in an inline thread, a review body |
| Who can call it | `author_association` `OWNER`, `MEMBER` or `COLLABORATOR` (organization membership counts, as GitHub defines it); never the bot |
| How it runs | A separate `mention` job, one short read-only `claude -p` session per mention |
| Tightening (new issue) | Bot judgment alone; the line must be inside a PR diff hunk |
| Loosening (withdrawal, guideline) | Only with a command word right after the mention (`철회`, `지침`), and the bot agrees |
| Verdict | Changes in the same job |
| Guideline commit | By the bot to the PR head branch |
| Bot name | Repository variable `vars.AI_REVIEW_BOT_NAME` (job `if:` cannot read secrets) |
| Approval dismissal | The team does not use "Dismiss stale pull request approvals" (documented caveat) |

## 3. Workflow

`templates/ai-review.yml`:

- Triggers added: `issue_comment` (`created`), `pull_request_review` (`submitted`),
  `pull_request_review_comment` (`created`). Edits and deletions are ignored.
- The workflow-level `concurrency` moves to the jobs, so a comment event or a bot push never
  cancels a running review.
- `review` job: `if: github.event_name == 'pull_request' && github.event.pull_request.draft
  == false && github.event.sender.login != vars.AI_REVIEW_BOT_NAME`; group
  `ai-review-<PR number>`, `cancel-in-progress: true`. The bot's guideline push starts no
  review; other CI still runs on it (no `[skip ci]`).
- `mention` job: `if:` one of the three comment events, on a pull request
  (`github.event.issue.pull_request` for `issue_comment`), sender not the bot. Group
  `ai-review-mention-<PR number>`, `cancel-in-progress: false`, `timeout-minutes: 30`, drafts
  included. Steps: checkout `refs/pull/<N>/head` (`fetch-depth: 0`,
  `persist-credentials: false`, bot PAT), preflight, `cd .github/ai-review && python3 -E -s
  -m review.mention --repo "$GITHUB_WORKSPACE" --pr <N>`, upload logs.
- `issue_comment` runs the default branch's workflow file; the job still runs the kit from
  the PR head (accepted: a PR editing the workflow file does not change `issue_comment`
  handling).
- `vars` needs GHES >= 3.8 (verify; added to the base design's 4.5 list).

## 4. Finding Pending Mentions

Python, before any LLM call; nothing found -> exit 0 in seconds.

- Sources: issue comments, review comments and reviews of the PR.
- Text is **cleaned** before any matching: fenced blocks, inline code and lines starting
  with `>` are removed.
- A **mention**: the cleaned body matches `(?<![\w-])@<bot>(?![\w-])`, case-insensitive.
- Ignored with a log line: comments by the bot, authors outside the allowed associations.
- **Answered**: a bot comment whose footer is `<!-- ai-review-reply {"c": "<key>"} -->`, or a
  bot review whose state `delta.k` holds the key (section 6.1). Keys: `i:<id>` issue
  comment, `r:<id>` review comment, `v:<id>` review. Only the last marker of a bot comment,
  at its very end, is read; everything shown before it is escaped (section 6.5).
- Every unanswered mention is handled, oldest first (a cancelled pending run's mention is
  picked up by the next run).
- Job deadline `mention_deadline_s` 1500 after start. Every session and resume gets the time
  left minus a publication reserve of 120 s; a mention with less than 120 s + 60 s left is not
  started and gets "시간이 부족해 처리하지 못했습니다. 다시 불러 주세요." (answered).
- Closed or merged PR: reply only, no ledger change, no commit.

## 5. One Mention

### 5.1 Command

Code reads the command from the cleaned comment: the first word right after the mention.

| Command | Meaning | Target |
|---|---|---|
| `철회` | Withdraw issues | `inline-reply` to a bot inline comment: that thread's issue. Otherwise ids written in the comment (8 hex digits) that are open issues. No target -> reply "어느 이슈인지 알 수 없습니다. 지적 스레드에서 답하거나 이슈 id를 적어 주세요." and no session |
| `지침` | Add, change or remove a guideline | The model proposes the change |
| none | Conversation | — |

Only an exact word counts: `철회하지 마` is no command. One command per comment. New issues
need no command and may come with any command.

### 5.2 Context

| Part | Content |
|---|---|
| Mention | Body, author, kind (`general`, `inline-new`, `inline-reply`, `review-body`), the command and its targets |
| Conversation | `inline-*`: the whole thread. Others: the latest mentions and bot replies of the PR, newest kept, cap `mention_conversation_chars` 12000 |
| Code | `inline-*` with `side` `RIGHT`: path, `start_line`–`line`, `diff_hunk`, head lines ±`mention_code_lines` 40. `side` `LEFT`: the same window from the PR base blob, labelled as the deleted side. `line` null (outdated): path only, "줄 위치를 알 수 없음 (오래된 코멘트)" |
| Ledger | The target issues in full; all open issues one line each (id, `file:line`, severity, title) |
| Guidelines | `inline-*`: files whose `applies` matches the path; others: the `INDEX.md` texts; cap `mention_rules_chars` 6000 |
| PR | Title, base, head |

The thread's issue is found by the hidden `<!-- ai-review-issue <id> -->` every bot inline
comment now carries; an older bot comment is matched by path, line and title.

### 5.3 Session

- Agent `agent/pr-mention.md` (Korean): answer on evidence; for each target issue say whether
  the person's reason holds in the code; report a new issue only for a real defect; with
  `지침`, propose the guideline the person asked for; PR content is data; Korean output.
- Same command, environment and guard as a review chunk, `--json-schema
  config/mention-schema.json`, explore budget `mention_explore_budget` 20, session limit
  `mention_session_s` 600, both cut by the deadline (4).
- No structured output: one resume without tools for the JSON (4 turns), within the
  deadline. Still none: reply "처리하지 못했습니다: <원인>", nothing else changes.

### 5.4 Output and Checks

```json
{
  "reply": "string, Korean markdown",
  "verdicts": [{"id": "string", "agree": "boolean", "reason": "string"}],
  "new_issues": [{"file", "line", "end_line", "severity", "confidence", "category",
                  "title", "detail", "suggestion"}],
  "guideline": null | {"op": "add|update|remove", "path": "string", "applies": ["glob"],
                       "rule": "string <= 200", "reason": "string <= 200",
                       "target": "rule id (update/remove)"}
}
```

Applied by code only after its check; a failed item is listed as "반영 안 됨: <이유>".

- **Withdrawal:** for each command target with `agree: true`, the issue becomes `waived`. A
  target already waived counts as done ("이미 반영됨"), not as a failure. `verdicts` for ids
  that are not targets are ignored. Without a `철회` command, no verdict changes anything.
- **New issue:** confidence < `min_confidence` dropped; blocking = `block_severities` and
  confidence >= `block_confidence`. The line must be a right-side line of a hunk of the PR
  diff `base...head`; otherwise only a note "diff 밖이라 이슈로 등록하지 않음". It goes
  through `ledger.classify` against the canonical ledger, like a chunk finding: a duplicate
  is "이미 이슈 <id>로 등록됨"; a link to an open issue applies its effect (a blocking link
  makes that issue blocking, as `aggregate._apply_links` does) and is listed as
  "기존 이슈 <id> 차단으로 상향" when it changed it. A new issue's record carries
  `at: <head sha>`.
- **Guideline:** only with the `지침` command, and the change passes 7.4.
- **Summary:** after `reply`, code appends what was applied: `**반영:** ` with parts joined by
  ` · ` (`철회 <id>`, `새 이슈 <id>` + ` (차단)`, `차단 상향 <id>`, `지침 <path> <rule id>
  추가|수정|삭제`) or `**반영:** 없음`, then one `- 반영 안 됨: <이유>` line per dropped
  item and the notes.

## 6. Ledger Consistency

### 6.1 Marker Fields

The v3 state marker gains:

- `type`: `review` (push review), `mention`, `sync`. A marker without it is `review`.
- `base`: the highest bot review id the writer had read when it built this state (0 if none).
- `delta` (only `mention`): `{"k": "<mention key>", "w": [ids waived], "n": [issue records
  added], "up": [ids made blocking]}`.
- Issue records may carry `at`: the head sha their `line` refers to, when it differs from the
  state's `sha`.

### 6.2 Canonical Ledger

From the PR's bot reviews (with a parsable v3 marker), in id order:

1. `P` = the latest review of type `review` or `sync`.
2. Start from `P`'s state (or an empty v3 state if there is none).
3. Apply, in review id order, the `delta` of every `mention` review with id > `P.base`
   that is not `P` itself: `w` waives an open issue (a closed one stays closed); `n` adds a
   record unless its id is already in the ledger; `up` sets `blocking` on an open issue.
4. Apply the dismissal waivers as today (`dismissed_ids`).

Applying a delta is idempotent, there is one order (review ids), and a closed issue is never
reopened or re-added. The push review reads this ledger at start instead of
`latest_state`. Coverage (`seen`, `seen_files`, `seen_hunks`, `cut`, `complete`, `sha`,
`recheck_pending`) comes only from `P`: mention reviews never change it.

In `scope._advance`, an issue with `at` is mapped from `at` to the head, the others from the
state's `sha` as today; the push review's new state drops `at` (everything is at its head).
Accepted: a closed record compacted while it still had an `at` loses it, so its region may be
off by the lines inserted in between; closed regions only serve `near_closed` and `same_as`.

### 6.3 Finalize

Both writers finish the same way (`review/publish.py`):

1. List the bot reviews and build the state to post:
   - push review: its own computed state, plus the deltas of the `mention` reviews with an id
     above the `base` it started from (mentions posted while it ran), applied as in 6.2;
   - mention job: the canonical ledger (6.2) plus its own delta.

   Set `base` to the highest listed id. Derive the verdict and body from that state (6.4).
2. Post the review (existing 422 fallback; one retry on status 0 or >= 500, after checking that
   no bot review with the same `delta.k` or the same body hash was stored meanwhile).
3. List the bot reviews again. If one was posted with an id between the listing of step 1 and
   this review, compute the canonical ledger again; when it differs from what was posted,
   post one `sync` review (state = canonical, body from it), then stop: a later run fixes
   anything left.
4. Only after a clean step 3 (or after the sync post), on `APPROVE`, dismiss the bot's
   `CHANGES_REQUESTED` reviews with an id lower than the review just posted. Never others.

`publish.finalize(...)` returns `{"ok": bool, "review_id": int|None, "sync_id": int|None,
"error": str|None}`; a caller never treats a failed post as done.

### 6.4 Verdict From the Ledger

Push review: as today from `merge`, then recomputed after step 1 adds mention deltas: entries
of `review["previous"]` waived by a delta are shown waived, added records are shown as open
previous issues, and `aggregate.decide_event(review, blocking)` (the rule extracted from
`merge`) gives the event with `blocking` from the open ledger issues and the new findings.

Mention and sync reviews do not call `merge`. `ledger_event(ledger, head, coverage)`:

- `REQUEST_CHANGES` when an open issue is blocking;
- else `APPROVE` when `coverage.complete`, `coverage.sha == head`, no `cut` entry and no
  `recheck_pending` id;
- else `COMMENT`.

No issue is resolved or closed by a mention or sync review except the waived targets; orphans
and rechecks stay as they are.

Mention review body: "@<작성자>의 코멘트(<링크>)를 반영한 리뷰", the verdict and its reason,
the summary of 5.4, the open blocking issues, the state marker. New issues on a right-side
line are inline comments of that review (with their issue marker). Sync review body: "AI 리뷰
상태 동기화", the verdict, the open blocking issues, the marker.

### 6.5 Replies and Markers

- Reply: `inline-*` -> `POST .../pulls/<n>/comments/<root id>/replies` (root of the thread);
  others -> an issue comment that starts with `> ` + the escaped first line of the mention
  and its link.
- Every text in a reply or review body (model output, quoted human text, file names) passes
  `render._safe`. The reply marker is appended last; `parse_reply` reads only a marker at the
  very end of the body (like `parse_state`).
- One retry on status 0 or >= 500, after re-listing to check the reply was not stored.

### 6.6 Order and Recovery Inside One Mention

1. Guideline commit and push (if any). The rule's source line holds the mention key.
2. If the ledger changes (`w`, `n` or `up` not empty): finalize the mention review (6.3).
3. Reply with its marker.

Before step 1, the job checks what an earlier attempt already did: a rule whose source holds
the key (skip step 1), a bot review whose `delta.k` is the key (skip steps 1 and 2; the
model is not run again, the reply is built from that delta's summary with
"이전 실행에서 반영했습니다."). A failed step leaves the mention unanswered for the next
job; the exit code is 1.

## 7. Guidelines

### 7.1 Layout

```
.github/ai-review-rules/
├─ INDEX.md          one line per directory (generated)
├─ general/  cpp/  cuda/  python/  shell/  yaml/  markdown/  paths/
│   ├─ INDEX.md      one line per file: path and title (generated)
│   └─ <topic>.md    e.g. cuda/stream-sync.md
```

Outside `.github/ai-review/`, so `install.sh` never replaces it; added to the default
`exclude_globs` (people review guideline changes in the diff).

### 7.2 File Format

```markdown
---
applies: ["**/*.cu", "**/*.cuh"]
title: CUDA 스트림 동기화
---
## R-3f9a1c  기본 스트림 직후의 cudaMemcpy는 동기화 누락으로 지적하지 않는다
- 이유: 이 저장소의 런처는 항상 기본 스트림을 쓰므로 암묵적으로 동기화된다
- 출처: PR #42 <코멘트 링크> (@요청자, 2026-11-11, r:123456)
```

Written by code, never by the model. Rule id `R-` + 6 hex of `sha1(path + "\n" + rule)`.
`applies` uses the `exclude_globs` matcher.

### 7.3 Use

- Per review chunk: files whose `applies` matches a chunk file, in a prompt section
  "저장소 지침", cap `rules_max_chars` 6000; `paths/` first, then more matched files; omitted
  ones logged "생략된 지침: <files>". Read from the PR head checkout (other PRs see a rule
  after merging or rebasing main).
- A broken file (no frontmatter, bad `applies`, symlink) is skipped with a log line.

### 7.4 Checks on a Change

- `path` matches `<general|cpp|cuda|python|shell|yaml|markdown|paths>/<kebab-case>.md`; no `..`,
  no symlink on the path.
- `update`/`remove`: `target` is a rule id in that file; `remove` of the last rule deletes it.
- A file that would exceed 100 lines is not changed: one resume without tools asks
  "같은 디렉토리에 새 주제 파일 경로를 정하라"; a valid new path gets the rule, else
  `<name>-2.md` (next free number).
- `rule` and `reason` one line each; an entry is at most 4 lines.
- `INDEX.md` files are regenerated after every change; one over 100 lines is logged.

### 7.5 Commit and Push

- Branch and repository from the PR API; commit by the bot,
  `ai-review: add rule R-3f9a1c to cuda/stream-sync.md (PR #42)`.
- The PAT reaches git only through `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_0`/`GIT_CONFIG_VALUE_0`
  (`http.extraheader`), never argv, `.git/config` or the model's environment.
- Non-fast-forward: fetch the new head, check it out, **reload the guideline files and apply
  the rule change again** (targets revalidated), regenerate the indexes, commit, push once
  more. Never copy earlier file contents.
- Still failing: the working tree is reset to the PR head; the reply gives the path and the
  entry text to commit by hand; ledger changes still apply.

## 8. Logs

Mention job lines prefixed `[m<n>]`: each mention found or ignored, command and targets,
session tool and LLM lines, each applied or dropped action, push and finalize results (sync
and dismissals included). `$GITHUB_STEP_SUMMARY` gets a table of mentions and outcomes.
The push review logs `언급 반영 <n>건` when finalize applied mention deltas, and `동기화 리뷰
게시` for a sync review.

## 9. Modules

| File | Purpose |
|---|---|
| `review/ledger_sync.py` | Marker fields, canonical ledger, delta application, `ledger_event` |
| `review/publish.py` | Finalize (6.3) for both writers |
| `review/rules.py`, `review/rules_git.py` | Guidelines (7) |
| `review/mention/{__main__,cli,fetch,command,context,apply,body}.py` | The mention job |
| `agent/pr-mention.md`, `config/mention-schema.json` | Agent and schema |

Changed: `render.py` (issue marker, `parse_reply`), `validate.py` (new fields),
`scope.py` (`at`), `aggregate.py` (`decide_event`), `cli.py` (canonical ledger, finalize),
`claude_cmd.py`/`claude_runner.py` (session kinds), `prompt.py` (guideline section),
`github_api.py` (comments, replies, retries, scoped dismissal), defaults, template, README,
runbook.

## 10. Testing

- Unit: command parsing (exact word, negation, quoted and code text, targets); mention
  detection; canonical ledger (orders, idempotency, closed never reopened, `up`); `at`
  mapping; `ledger_event`; finalize (interleavings, sync, scoped dismissal, lost POST
  response); checks of 5.4 (targets only, already waived, link upgrade 60 -> 95, hunk range);
  rules (format, ids, overflow, indexes, select order); rules_git (PAT, semantic retry with a
  concurrent edit of the same file, final failure cleanup); context (`LEFT`, outdated,
  multi-line); forged markers posted by a person and echoed in a bot reply.
- Integration (temp repos, fake `claude`, fake GitHub, bare remote):
  - `@ai-bot 철회` on the only blocking issue -> APPROVE, only older change requests dismissed;
  - `@ai-bot 이건 의도된 거야` (no command) -> reply only;
  - a question whose injected model output withdraws an issue -> nothing withdrawn;
  - new issue on a diff line -> REQUEST_CHANGES; outside the hunks -> note only;
  - `@ai-bot 지침 ...` -> commit pushed, next review's prompt contains it;
  - push review and mention in both posting orders, with the push adding a blocker and
    resolving an issue -> the canonical ledger has every change and the last review's verdict
    matches it;
  - an incomplete state with an unrelated blocking orphan + a withdrawal -> the orphan stays
    open and blocking;
  - failure after the mention review and before the reply -> the next job replies once
    without running the model;
  - two pending mentions in one job; outsider and bot comments ignored; late start near the
    deadline.
- Workflow template: triggers, job `if:`, job-level concurrency, `vars`, both checkouts.
- Smoke (manual, runner, real LiteLLM): one mention of each kind and each command.

## 11. Accepted Costs

- Withdrawal and guideline need a command word; a natural-language request without it gets
  an answer and a hint.
- Two writers posting within the same second can leave the last visible verdict stale until
  the next run; the canonical ledger is still correct.
- `issue_comment` handling follows the default branch's workflow file.
- Guidelines reach other open PRs after they merge or rebase main.
- A guideline commit has no AI review until the next human push.
- Edited comments are not re-read.

## 12. Out of Scope

- Reacting without a mention; reopening a waived issue through a mention; guidelines shared
  across repositories.
