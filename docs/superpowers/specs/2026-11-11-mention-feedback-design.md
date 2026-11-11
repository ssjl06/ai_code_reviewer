# Mention Feedback — Design

- Date: 2026-11-11
- Status: Draft (awaiting review), rev 3
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
- Rev 3, after the second Codex review: every serious finding of both reviews came from one
  premise, that a push review and a mention job may run at the same time on one PR. Rev 2
  repaired the resulting orders after the fact (deltas, `base`, `at`, sync reviews,
  post-checks, scoped dismissals) and each repair opened new holes: a stale sync erased a
  newer push review, two concurrent adds lost a blocking flag, a delta with only waivers was
  dropped, recovery was unreachable. Rev 3 removes the premise: **one job per PR at a time**
  (section 3). Each run reads the latest state, changes it and writes it; all of rev 2's
  section 6 machinery is gone. A run brings the ledger to the head before it handles
  mentions, so a mention never works on a stale ledger. Kept from the second review: the
  approval flag is stored instead of inferred from `complete`, a commit trailer is the
  receipt of every guideline change (removals included), the deleted side is read from the
  merge base, recovery is staged.

## 1. Goal

A person mentions the bot (`@<bot name>`) in a pull request and the bot responds to that
comment in its context. In order of priority:

1. **Conversation (B).** The bot answers the question or objection.
2. **Verdict change (A).** A withdrawal the person commands and the bot agrees with, or a
   defect the bot confirms, changes the ledger at once, and a review with the updated
   verdict is posted in the same run.
3. **Guidelines (C).** A guideline the person commands is committed by the bot to a file in
   the PR branch; later reviews read the guidelines that apply to their files.

Success criteria:

- A mention gets one reply, normally within a few minutes; when a review of the PR is
  running, after that review.
- Ledger changes from mentions and from push reviews are never lost: one run at a time
  reads, changes and writes the ledger (sections 3 and 6).
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
| How it runs | One job per PR at a time: it reviews the head if needed, then handles the pending mentions, one short read-only `claude -p` session each |
| Tightening (new issue) | Bot judgment alone; the line must be inside a PR diff hunk |
| Loosening (withdrawal, guideline) | Only with a command word right after the mention (`철회`, `지침`), and the bot agrees |
| Verdict | Changes in the same run |
| Guideline commit | By the bot to the PR head branch |
| Bot name | Repository variable `vars.AI_REVIEW_BOT_NAME` (job `if:` cannot read secrets) |
| Approval dismissal | The team does not use "Dismiss stale pull request approvals" (documented caveat) |

## 3. Workflow

One job, `review`, serves every event, and at most one runs per PR.

`templates/ai-review.yml`:

- Triggers added: `issue_comment` (`created`), `pull_request_review` (`submitted`),
  `pull_request_review_comment` (`created`). Edits and deletions are ignored.
- Job `if:` — the sender is not `vars.AI_REVIEW_BOT_NAME`, and either
  - `pull_request` event and the PR is not a draft, or
  - a comment event on a pull request (`github.event.issue.pull_request` for
    `issue_comment`) whose body contains `@<bot name>` (`contains()`, a coarse filter so an
    ordinary comment starts no runner job; section 4 decides exactly).

  The bot's own guideline push therefore starts nothing; other CI still runs on it (no
  `[skip ci]`).
- The workflow-level `concurrency` moves to the job: group `ai-review-<PR number>`
  (`github.event.pull_request.number || github.event.issue.number`),
  `cancel-in-progress: ${{ github.event_name == 'pull_request' }}`.
  - A push cancels whatever runs for that PR (as today: a review of an old head is
    worthless) and its own run does all the work below.
  - A comment event never cancels a running job; it waits for it. GitHub keeps one pending
    run per group, so a newer event replaces an older pending one. Nothing is lost, because
    a run does not handle "its" event: it does everything outstanding for the PR.
- `timeout-minutes: 350` (review deadline 300 min + mention phase 25 min + margin).
- Steps: checkout `refs/pull/<N>/head` for every event (`fetch-depth: 0`,
  `persist-credentials: false`, bot PAT), preflight, `cd .github/ai-review && python3 -E -s
  -m review --repo "$GITHUB_WORKSPACE" --pr <N> ...`, upload logs.
- `issue_comment` runs the default branch's workflow file; the job still runs the kit from
  the PR head (accepted: a PR editing the workflow file does not change `issue_comment`
  handling, and mentions work only once the new workflow file is on the default branch).

One run (`python3 -m review`), in this order:

1. **Review phase**, skipped for a draft, closed or merged PR: the existing review. On a
   head the ledger already covers it ends at once (`unchanged`), which is the normal case
   of a comment event. A comment event whose PR head was never reviewed to the end (an
   earlier run failed or was cancelled) gets that review first.
2. **Mention phase** (sections 4 to 6): every pending mention, oldest first, each against
   the state the previous step left.
3. If the mention phase pushed a guideline commit: the review phase once more, so the state
   follows the new head (the guideline directory is excluded, so this normally ends at
   once).

The exit code is the worst of the phases. `--dry-run` runs only the review phase.

To verify on the GHES (added to the base design's 4.5 list): `vars` (>= 3.8); an expression
in `cancel-in-progress`; and that a job skipped by `if:` neither cancels nor replaces a run
of its group. If a skipped job does take part, the group expression gives skipped events a
group of their own (`ai-review-skip-<run id>`), repeating the `if:` condition.

## 4. Finding Pending Mentions

Python, before any LLM call; nothing found -> the phase ends in seconds.

- Sources: issue comments, review comments and reviews of the PR.
- Text is **cleaned** before any matching: fenced blocks, inline code and lines starting
  with `>` are removed.
- A **mention**: the cleaned body matches `(?<![\w-])@<bot>(?![\w-])`, case-insensitive.
- Ignored with a log line: comments by the bot, authors outside the allowed associations.
- **Answered**: a bot comment whose footer is `<!-- ai-review-reply {"c": "<key>"} -->`,
  and nothing else (a mention whose review was posted but whose reply is missing is not
  answered; section 6.4 finishes it). Keys: `i:<id>` issue comment, `r:<id>` review
  comment, `v:<id>` review. Only the last marker of a bot comment, at its very end, is
  read; everything shown before it is escaped (section 6.3).
- Every unanswered mention is handled, oldest first, whichever event started the run.
- Phase deadline `mention_deadline_s` 1500 after the phase starts. Every session and resume
  gets the time left minus a publication reserve of 120 s; a mention with less than 120 s +
  60 s left is not started and gets "시간이 부족해 처리하지 못했습니다. 다시 불러 주세요." (answered).
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
| Code | `inline-*` with `side` `RIGHT`: path, `start_line`–`line`, `diff_hunk`, head lines ±`mention_code_lines` 40. `side` `LEFT`: the same window from the blob at the merge base of base and head (the left side of the PR diff), labelled as the deleted side. `line` null (outdated): path only, "줄 위치를 알 수 없음 (오래된 코멘트)" |
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
  `mention_session_s` 600, both cut by the phase deadline (4).
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
  diff `base...head`; otherwise only a note "diff 밖이라 이슈로 등록하지 않음". The ledger's
  lines are those of the state's `sha`. When that is not the head (a draft, or the review
  phase failed), the issue is registered only if its file's blob is the same at both
  commits, so the line means the same in both; otherwise a note "이 파일은 아직 리뷰되지
  않은 변경이 있어 이슈로 등록하지 않음" (the defect is still explained in the reply). It
  goes through `ledger.classify` against the ledger, like a chunk finding: a duplicate is
  "이미 이슈 <id>로 등록됨"; a link to an open issue applies its effect (a blocking link
  makes that issue blocking, as `aggregate._apply_links` does) and is listed as
  "기존 이슈 <id> 차단으로 상향" when it changed it.
- **Guideline:** only with the `지침` command, and the change passes 7.4.
- **Summary:** after `reply`, code appends what was applied: `**반영:** ` with parts joined by
  ` · ` (`철회 <id>`, `새 이슈 <id>` + ` (차단)`, `차단 상향 <id>`, `지침 <path> <rule id>
  추가|수정|삭제`) or `**반영:** 없음`, then one `- 반영 안 됨: <이유>` line per dropped
  item and the notes.

## 6. Ledger and Publication

### 6.1 Single Writer

The ledger is the state marker of the latest bot review, with the dismissal waivers applied
(`latest_state`, `apply_waivers`), exactly as the push review reads it today. Because one
run at a time works on a PR (section 3), nothing else can post a state between a run's read
and its write. There is no merge of states, no ordering rule and no post-check.

The v3 state marker gains three fields:

- `approvable` (boolean), written by the review phase: nothing but open issues stands
  between this review and an approval — it had targets, every chunk finished, no file was
  skipped, truncated or cut (`merge`'s `no_targets`, `incomplete`, `failed_all`, `gaps`,
  `cut_files` all empty). A marker without the field counts as `false`.
- `unclear` (ids), written by the review phase: the blocking issues it could neither
  confirm nor resolve. `merge` answers those with `COMMENT`, not `REQUEST_CHANGES`.
- `m`, only in a mention review: `{"k": "<mention key>", "w": [ids waived], "n": [ids
  added], "up": [ids made blocking], "g": "<path> <rule id> <op>" or absent}`. It is a
  receipt for recovery (6.4) and the source of the summary; no ledger is computed from it.

The review phase carries `approvable` and `unclear` over when it settles without a model
call, and drops `m`.

### 6.2 The Mention Review

Posted when a mention changed the ledger (a waiver, a new issue or an upgrade). Its state is
the state it read with those changes applied: `sha`, `seen*`, `cut`, `complete`,
`recheck_pending`, `approvable`, `unclear` unchanged; a waived target becomes `waived`; a
new record is added as open; an upgraded issue gets `blocking`. No other issue is touched:
no orphan is closed, nothing is rechecked. The state passes `fit_state` like any other, so
the marker size limit holds after the additions.

It does not call `merge`. `ledger_event(state, current)`:

- `REQUEST_CHANGES` when an open issue is blocking and its id is not in `unclear`;
- else `APPROVE` when `state.approvable`, no id of `unclear` is still open, and `current`:
  `scope.determine` on this state and the head says `unchanged` (the head is covered and
  nothing waits for a recheck);
- else `COMMENT`.

Body: "@<작성자>의 코멘트(<링크>)를 반영한 리뷰", the verdict and its reason, the summary of
5.4, the open blocking issues, the state marker. New issues on a right-side line are inline
comments of that review, rendered by `render_inline` from the full finding (detail,
suggestion, category, confidence) with their issue marker; the 422 fallback moves them into
the body as today. On `APPROVE` the bot's earlier change requests are dismissed as today
(`dismiss_change_requests`); with one writer there is no newer review to hit by mistake.

### 6.3 Replies and Markers

- Reply: `inline-*` -> `POST .../pulls/<n>/comments/<root id>/replies` (root of the thread);
  others -> an issue comment that starts with `> ` + the escaped first line of the mention
  and its link.
- Every text in a reply or review body (model output, quoted human text, file names) passes
  `render._safe`. The reply marker is appended last; `parse_reply` reads only a marker at the
  very end of the body (like `parse_state`). Markers are read only from the bot's own
  comments and reviews.
- A POST that fails with status 0 or >= 500 is retried once, after listing again to check
  that the review (by `m.k`) or the reply (by its marker) was not stored.

### 6.4 Order and Recovery Inside One Mention

1. Guideline commit and push (if any). The commit message ends with the trailer
   `AI-Review-Mention: <key>`.
2. If the ledger changes: the mention review (6.2), its marker holding `m`.
3. The reply with its marker. Only this makes the mention answered.

A run can die or be cancelled by a push between any two steps. The next run finds the
mention unanswered and looks for the receipts first:

- a bot review whose `m.k` is the key: the model is not run again. Step 3 is done from `m`:
  "이전 실행에서 반영했습니다." and the summary.
- else a commit on the PR branch with the trailer: the session runs again, and step 1 is
  skipped; the guideline is reported from that commit (`g`), whatever the model proposes
  this time.
- else: the mention is handled from the start.

A failed step leaves the mention unanswered for the next run and makes the exit code 1.
The steps are idempotent on their own as well: waiving a waived issue, or adding an issue
`classify` finds already registered, changes nothing.

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
  `ai-review: add rule R-3f9a1c to cuda/stream-sync.md (PR #42)`, with the trailer
  `AI-Review-Mention: <key>` (6.4).
- A PR from a fork, or a branch the PAT cannot push to: no commit; the reply gives the path
  and the entry text to commit by hand.
- The PAT reaches git only through `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_0`/`GIT_CONFIG_VALUE_0`
  (`http.extraheader`), never argv, `.git/config` or the model's environment.
- Non-fast-forward: fetch the new head, check it out, **reload the guideline files and apply
  the rule change again** (targets revalidated), regenerate the indexes, commit, push once
  more. Never copy earlier file contents.
- Still failing: the working tree is reset to the PR head; the reply gives the path and the
  entry text to commit by hand; ledger changes still apply.

## 8. Logs

Mention phase lines prefixed `[m<n>]`: each mention found or ignored, command and targets,
session tool and LLM lines, each applied or dropped action, each receipt found from an
earlier run, push, review and reply results (dismissals included). `$GITHUB_STEP_SUMMARY`
gets a table of mentions and outcomes. `[main]` logs which phases ran and why
(`리뷰 단계 건너뜀: 초안`, `언급 <n>건 처리`).

## 9. Modules

| File | Purpose |
|---|---|
| `review/run.py` | The phases of one run (3) and the exit code |
| `review/mention/{cli,fetch,command,context,apply,body}.py` | The mention phase |
| `review/mention/ledger.py` | Applying a mention's changes to a state, `ledger_event` |
| `review/rules.py`, `review/rules_git.py` | Guidelines (7) |
| `agent/pr-mention.md`, `config/mention-schema.json` | Agent and schema |

Changed: `__main__.py` (calls `run`), `cli.py` (review phase callable), `render.py` (issue
marker, `parse_reply`), `validate.py` (`approvable`, `unclear`, `m`), `scope.py` and
`aggregate.py` (write `approvable` and `unclear`), `claude_cmd.py`/`claude_runner.py`
(session kinds), `prompt.py` (guideline section), `github_api.py` (comments, replies, one
retry), defaults, template, README, runbook.

## 10. Testing

- Unit: command parsing (exact word, negation, quoted and code text, targets); mention
  detection; applying changes to a state (waive, add, upgrade, nothing else touched, size
  fit); `ledger_event` (blocking, `unclear`, `approvable` false or missing, not current);
  `approvable` and `unclear` written by `merge`/`next_state` and carried by `settle_state`;
  checks of 5.4 (targets only, already waived, link upgrade 60 -> 95, hunk range, state not
  at the head with the same and with a different blob); rules (format, ids, overflow,
  indexes, select order); rules_git (PAT, trailer, semantic retry with a concurrent edit of
  the same file, final failure cleanup); context (`LEFT` from the merge base, outdated,
  multi-line); forged markers posted by a person and echoed in a bot reply.
- Integration (temp repos, fake `claude`, fake GitHub, bare remote):
  - `@ai-bot 철회` on the only blocking issue -> APPROVE, change requests dismissed;
  - the same on a review that had no targets, or with a cut file -> COMMENT, not APPROVE;
  - `@ai-bot 이건 의도된 거야` (no command) -> reply only;
  - a question whose injected model output withdraws an issue -> nothing withdrawn;
  - new issue on a diff line -> REQUEST_CHANGES; outside the hunks -> note only;
  - `@ai-bot 지침 ...` -> commit pushed with the trailer, the state follows the new head,
    the next review's prompt contains the rule;
  - a comment event on a head never reviewed -> the review is posted first, then the reply,
    and a new issue from the mention lands in the state of that review's head;
  - an incomplete state with an unrelated blocking orphan + a withdrawal -> the orphan stays
    open and blocking;
  - recovery, one test per gap: dead after the push (session runs, no second commit), after
    the mention review (no session, one reply), a guideline removal after the push;
  - two pending mentions in one run, the second sees the first's changes; outsider and bot
    comments ignored; late start near the deadline; draft PR (no review phase, conversation
    works); closed PR (reply only).
- Workflow template: triggers, job `if:` (bot sender, draft, coarse `contains`), the group
  for each event, `cancel-in-progress` only for `pull_request`, `vars`, the checkout ref.
- Smoke (manual, runner, real LiteLLM): one mention of each kind and each command; a
  mention while a review runs (waits, then answers); a push while a mention is handled
  (cancelled, answered once by the push's run); an ordinary comment (no job, nothing
  cancelled).

## 11. Accepted Costs

- Withdrawal and guideline need a command word; a natural-language request without it gets
  an answer and a hint.
- A mention made while a review runs is answered after that review (up to the review
  deadline in the worst case); a push during a mention's handling cancels it and the
  push's run answers it after its review.
- A comment event on a PR whose last review failed starts that review again before it
  answers.
- `issue_comment` handling follows the default branch's workflow file.
- Guidelines reach other open PRs after they merge or rebase main.
- A guideline commit has no AI review until the next human push.
- Edited comments are not re-read.

## 12. Out of Scope

- Reacting without a mention; reopening a waived issue through a mention; guidelines shared
  across repositories.
