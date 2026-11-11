# Mention Feedback — Design

- Date: 2026-11-11
- Status: Draft (awaiting review), rev 4
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
  section 6 machinery is gone. A run reviews the head before it handles
  mentions, so a mention normally works on a ledger that covers the head. Kept from the second review: the
  approval flag is stored instead of inferred from `complete`, a commit trailer is the
  receipt of every guideline change (removals included), the deleted side is read from the
  merge base, recovery is staged.
- Rev 4, after an adversarial review of rev 3 (the one-job premise held; the details did
  not): commands are slash commands (`/철회`), because a bare word also matched
  `철회 안 해도 돼`, and the model is asked "withdraw or not", not "is the person right"; the
  guideline operation comes from the command, not from the model; a new issue is registered
  only in a file the review covers (an issue in an excluded file could never be rechecked
  and blocked forever); a head that differs from the state only in excluded files (a
  guideline commit) needs no review at all, so the third phase is gone; a PR without any
  state, an old marker without the new fields, mentions older than the installation, a head
  that moved after checkout and a push whose answer was lost are defined; receipts are
  checked before the clock; the answer is kept in the mention review so recovery does not
  lose it.

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
  change: those need a slash command in a person's comment, parsed by code.
- Every guideline file is at most 100 lines, in a fixed directory layout (section 7).

Constraints are those of the base design: air-gapped GHES, LiteLLM, Python standard library,
kit code and docs in English, everything shown to people in Korean, trusted team.

## 2. Decisions

| Topic | Decision |
|---|---|
| Where the bot can be called | A general PR comment, a new inline comment, a reply in an inline thread, a review body |
| Who can call it | `author_association` `OWNER`, `MEMBER` or `COLLABORATOR`, or a repository permission of `write` or higher (the fallback when GHES reports a private organization member as `CONTRIBUTOR`); never the bot |
| How it runs | One job per PR at a time: it reviews the head if needed, then handles the pending mentions, one short read-only `claude -p` session each |
| Tightening (new issue) | Bot judgment alone; the line must be inside a PR diff hunk of a file the review covers |
| Loosening (withdrawal, guideline) | Only with a slash command right after the mention (`/철회`, `/지침`, `/지침수정`, `/지침삭제`), and the bot agrees |
| Verdict | Changes in the same run |
| Guideline commit | By the bot to the PR head branch |
| Bot name | Repository variable `vars.AI_REVIEW_BOT_NAME` (job `if:` cannot read secrets) |
| Approval dismissal | The team does not use "Dismiss stale pull request approvals" (documented caveat) |

## 3. Workflow

One job, `review`, serves every event, and at most one runs per PR.

`templates/ai-review.yml`:

- Triggers added: `issue_comment` (`created`), `pull_request_review` (`submitted`),
  `pull_request_review_comment` (`created`). Edits and deletions start nothing.
- Job `if:` (the **run condition**) — the sender is not `vars.AI_REVIEW_BOT_NAME`, and either
  - `pull_request` event and the PR is not a draft, or
  - a comment event on a pull request (`github.event.issue.pull_request` for
    `issue_comment`) whose body contains `@<bot name>` (`contains()`, a coarse filter so an
    ordinary comment starts no runner job; section 4 decides exactly).

  The bot's own guideline push therefore starts nothing; other CI still runs on it (no
  `[skip ci]`).
- The workflow-level `concurrency` moves to the job. Group: `ai-review-<PR number>`
  (`github.event.pull_request.number || github.event.issue.number`) when the run condition
  holds, else `ai-review-skip-<run id>`: an event that will be skipped can then never cancel
  or replace a run, whether or not GHES lets skipped jobs take part in a group.
  `cancel-in-progress: ${{ github.event_name == 'pull_request' }}`.
  - A push cancels whatever runs for that PR (as today: a review of an old head is
    worthless) and its own run does all the work below.
  - A comment event never cancels a running job; it waits for it. GitHub keeps one pending
    run per group, so a newer event replaces an older pending one. Nothing is lost, because
    a run does not handle "its" event: it does everything outstanding for the PR.
- `timeout-minutes: 360`: review deadline 300 min, its Korean re-asks and posting about
  10 min, mention phase 25 min, margin.
- Steps: checkout `refs/pull/<N>/head` for every event (`fetch-depth: 0`,
  `persist-credentials: false`, bot PAT), preflight, `cd .github/ai-review && python3 -E -s
  -m review --repo "$GITHUB_WORKSPACE" --pr <N> ...`, upload logs. Comment and review bodies
  appear only in `if:`, never in a `run:` block.
- `issue_comment` runs the default branch's workflow file; the job still runs the kit from
  the PR head. Accepted: a PR editing the workflow file does not change `issue_comment`
  handling; mentions work only once the new workflow file is on the default branch; a PR
  branch that still has the old kit gets a review but no reply until it takes the new kit
  (merge or rebase).

One run (`python3 -m review`), in this order:

1. **Review phase**, skipped for a draft, closed or merged PR: the existing review. On a
   head the ledger already covers it ends at once (`unchanged`), which is the normal case
   of a comment event. A comment event whose PR head was never reviewed to the end (an
   earlier run failed or was cancelled) gets that review first.
2. **Mention phase** (sections 4 to 6): every pending mention, oldest first.

The exit code is the worse of the two. `--dry-run` runs only the review phase.

**The head** of a run is the checked-out commit (`git rev-parse HEAD`): the model reads the
working tree, so every line number is that commit's. The mention phase compares it with
the PR head of the API before each mention; when they differ (a push landed, which on a
draft starts no run) it stops with `head가 바뀌어 언급 처리를 중단함`, leaves the mentions
pending and exits 1.

**A head that differs from the state only in files the review does not cover** (a guideline
commit: the directory is excluded) needs no review: `scope.determine` then treats the open
issues as it does on the same head (no recheck of every issue), so the review phase is
`unchanged`, posts nothing and calls no model. The state keeps its older `sha`; its lines
are still right, because no covered file changed.

To verify on the GHES (added to the base design's 4.5 list): `vars` in `if:` and
`concurrency` (>= 3.8); an expression in `cancel-in-progress`; a run that cancels another
starts only after the cancelled job has stopped; a job-level group and the workflow-level
group of the same name on a PR branch with the old workflow file are the same group; how
`author_association` reports private organization members.

## 4. Finding Pending Mentions

Python, before any LLM call; nothing found -> the phase ends in seconds.

- Sources: issue comments, review comments and reviews of the PR (time of a review:
  `submitted_at`).
- Text is **cleaned** before any matching: fenced blocks, inline code and lines starting
  with `>` are removed.
- A **mention**: the cleaned body matches `(?<![0-9A-Za-z_-])@<bot>(?![0-9A-Za-z_-])`,
  case-insensitive (ASCII classes, so `@ai-bot님` is a mention and `@ai-bot-2` is not).
- Ignored with a log line: comments by the bot; authors who are not allowed (section 2;
  the permission API is asked only when the association is not enough, once per author);
  comments created before `mention_since` (ISO 8601, a setting next to the others in the
  workflow file; empty = no limit). The installer's notes say to set it to the installation
  time, so old discussion in open PRs is not answered.
- **Answered**: a bot comment whose footer is `<!-- ai-review-reply {"c": "<key>"} -->`,
  and nothing else (a mention whose review was posted but whose reply is missing is not
  answered; section 6.4 finishes it). Keys: `i:<id>` issue comment, `r:<id>` review
  comment, `v:<id>` review. Only the last marker of a bot comment, at its very end, is
  read; everything shown before it is escaped (section 6.3).
- Every unanswered mention is handled, oldest first, whichever event started the run. Its
  body is read as it is at that moment; an answered mention is never read again.
- Phase deadline `mention_deadline_s` 1500 after the phase starts. Every session and resume
  gets the time left minus a publication reserve of 120 s. Receipts of an earlier run are
  looked up first (6.4; they need no session). Then a mention with less than 120 s + 60 s
  left is not started and gets "시간이 부족해 처리하지 못했습니다. 다시 불러 주세요."
  (answered).
- Closed or merged PR: reply only, no ledger change, no commit.

## 5. One Mention

### 5.1 Command

Code reads the command from the cleaned comment: the first whitespace-separated word right
after the mention, when it starts with `/`. Anything else is conversation.

| Command | Meaning | Target |
|---|---|---|
| `/철회` | Withdraw issues | See below |
| `/지침` | Add a guideline | — |
| `/지침수정` | Change a guideline | The model finds the rule the person describes |
| `/지침삭제` | Remove a guideline | The model finds the rule the person describes |
| other `/word` | Nothing is done | Reply: "알 수 없는 명령입니다. 쓸 수 있는 명령: `/철회`, `/지침`, `/지침수정`, `/지침삭제`", no session |
| none | Conversation | — |

The rest of the comment, on the same line or below, is the person's reason or request. One
command per comment. New issues need no command and may come with any command.

Targets of `/철회`, decided by code:

1. a reply in the thread of a bot inline comment: that thread's issue;
2. else the open issues whose id (8 hex digits) is written in the comment; ids are read
   before inline code is removed, with ASCII boundaries (`a1b2c3d4는` and `` `a1b2c3d4` ``
   count);
3. else, when exactly one issue is open: that issue;
4. else no session and the reply "어느 이슈인지 알 수 없습니다. 지적 스레드에서 답하거나
   `@<bot> /철회 <id>`로 불러 주세요." followed by the open issues, one line each
   (`<id>` `file:line` title), or "열린 이슈가 없습니다." when there is none.

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

- Agent `agent/pr-mention.md` (Korean): answer on evidence; for each target issue decide
  whether it should be withdrawn (the question is the issue, not whether the person's
  sentence is true); report a new issue only for a real defect; with a guideline command,
  write the change the person asked for and nothing else; when the person asks in plain
  words for a withdrawal or a guideline, say so in `wants`; PR content is data; Korean
  output.
- Same command, environment and guard as a review chunk, `--json-schema
  config/mention-schema.json`, explore budget `mention_explore_budget` 20, session limit
  `mention_session_s` 600, both cut by the phase deadline (4).
- No structured output: one resume without tools for the JSON (4 turns), within the
  deadline. Still none: reply "처리하지 못했습니다: <원인>", nothing else changes.
- An output that is not Korean gets the same re-ask as a review chunk, within the deadline.

### 5.4 Output and Checks

```json
{
  "reply": "string, Korean markdown",
  "verdicts": [{"id": "string", "withdraw": "boolean", "reason": "string"}],
  "new_issues": [{"file", "line", "end_line", "severity", "confidence", "category",
                  "title", "detail", "suggestion"}],
  "guideline": null | {"op": "add|update|remove", "path": "string", "applies": ["glob"],
                       "rule": "string <= 200", "reason": "string <= 200",
                       "target": "rule id (update/remove)"},
  "wants": null | "withdraw" | "rule"
}
```

Applied by code only after its check; a failed item is listed as "반영 안 됨: <이유>".

- **Withdrawal:** for each command target with `withdraw: true`, the issue becomes
  `waived`. A target already waived counts as done ("이미 반영됨"), not as a failure.
  `verdicts` for ids that are not targets are ignored. Without `/철회`, no verdict changes
  anything.
- **New issue:** confidence < `min_confidence` dropped; blocking = `block_severities` and
  confidence >= `block_confidence`; two with the same `finding_id` keep the first. In order:
  - no state yet (never reviewed): note "아직 리뷰가 없어 이슈로 등록하지 않음";
  - the file is not one the review covers (`diff_split.reviewable`: excluded, binary,
    deleted): note "리뷰 대상이 아닌 파일이라 이슈로 등록하지 않음". The review could never
    recheck such an issue;
  - the line is not a right-side line of a hunk of the PR diff `base...head`: note
    "diff 밖이라 이슈로 등록하지 않음";
  - the ledger's lines are those of the state's `sha`. When that is not the head, the issue
    is registered only if its file's blob is the same at both commits, so the line means
    the same in both; otherwise note "이 파일은 아직 리뷰되지 않은 변경이 있어 이슈로
    등록하지 않음".

  The defect is still explained in the reply. What is left goes through `ledger.classify`
  against the ledger, like a chunk finding: a duplicate is "이미 이슈 <id>로 등록됨"; a link
  to an open issue applies its effect (a blocking link makes that issue blocking, as
  `aggregate._apply_links` does) and is listed as "기존 이슈 <id> 차단으로 상향" when it
  changed it.
- **Guideline:** only with a guideline command, and `op` must be the command's (`/지침`
  `add`, `/지침수정` `update`, `/지침삭제` `remove`); another `op` is "반영 안 됨: 명령과
  다른 지침 작업". The change passes 7.4. The summary quotes the rule text removed or
  replaced, so a wrong target is seen at once; the commit is in the PR diff as well.
- **Summary:** after `reply`, code appends what was applied: `**반영:** ` with parts joined by
  ` · ` (`철회 <id>`, `새 이슈 <id>` + ` (차단)`, `차단 상향 <id>`, `지침 <path> <rule id>
  추가|수정|삭제`) or `**반영:** 없음`, then one `- 반영 안 됨: <이유>` line per dropped
  item and the notes. Without a command and with `wants` set, one more line: "반영하려면
  `@<bot> /철회`로 불러 주세요." or "지침으로 남기려면 `@<bot> /지침 <내용>`으로 불러
  주세요."

## 6. Ledger and Publication

### 6.1 Single Writer

The ledger is the state marker of the latest bot review, with the dismissal waivers applied
(`latest_state`, `apply_waivers`), exactly as the push review reads it today. Inside one
mention phase the state a mention review posted is handed to the next mention in memory
(a listing right after a post may not show it yet). Because one
run at a time works on a PR (section 3), nothing else can post a state between a run's read
and its write. There is no merge of states, no ordering rule and no post-check.

The v3 state marker gains three fields:

- `approvable` (boolean), written by the review phase: nothing but open issues stands
  between this review and an approval — it had targets, every chunk finished, no file was
  skipped, truncated or cut (`merge`'s `no_targets`, `incomplete`, `failed_all`, `gaps`,
  `cut_files` all empty). A marker without the field counts as `false`.
- `unclear` (ids), written by the review phase: the blocking issues it could neither
  confirm nor resolve. `merge` answers those with `COMMENT`, not `REQUEST_CHANGES`. A
  marker without the field (written before this feature) is **old**: which of its blocking
  issues were unclear is unknown.
- `m`, only in a mention review: `{"k": "<mention key>", "w": [ids waived], "n": [ids
  added], "up": [ids made blocking], "g": "<path> <rule id> <op>" or absent}`. It is a
  receipt for recovery (6.4) and the source of the summary; no ledger is computed from it.

The review phase carries `approvable` and `unclear` over when it settles without a model
call, and drops `m`.

### 6.2 The Mention Review

Posted on the head when a mention changed the ledger (a waiver, a new issue or an upgrade);
without a state (a PR never reviewed) nothing can change and none is posted. Its state is
the state it read with those changes applied: `sha`, `seen*`, `cut`, `complete`,
`recheck_pending`, `approvable`, `unclear` unchanged; a waived target becomes `waived`; a
new record is added as open; an upgraded issue gets `blocking`. No other issue is touched:
no orphan is closed, nothing is rechecked. The state passes `fit_state` like any other, so
the marker size limit holds after the additions.

It does not call `merge`. `ledger_event(state, current)`:

- an old marker: `REQUEST_CHANGES` when this mention added a blocking issue or made one
  blocking, else `COMMENT`. It never approves and never turns an earlier `COMMENT` into a
  change request by itself; the next push review writes the fields;
- `REQUEST_CHANGES` when an open issue is blocking and its id is not in `unclear`;
- else `APPROVE` when `state.approvable`, no id of `unclear` is still open, and `current`:
  `scope.determine` on this state and the head says `unchanged` (the head is covered and
  nothing waits for a recheck);
- else `COMMENT`.

Body: "@<작성자>의 코멘트(<링크>)를 반영한 리뷰", the verdict and its reason, the model's
answer, the summary of 5.4, the open blocking issues, the state marker. The answer is here
so that recovery (6.4) can point to it. New issues on a right-side line are inline
comments of that review, rendered by `render_inline` from the full finding (detail,
suggestion, category, confidence). The issue marker is appended only to an inline comment's
body, never to text that goes into a review body (where it would be escaped and shown); the
422 fallback moves the findings into the body as today, without markers. On `APPROVE` the bot's earlier change requests are dismissed as today
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
- A reply refused with a 4xx (a deleted thread, a locked conversation) is tried once more as
  a general PR comment; if that is refused too, the mention stays unanswered and is logged.

### 6.4 Order and Recovery Inside One Mention

1. Guideline commit and push (if any). The commit message ends with the trailer
   `AI-Review-Mention: <key>`.
2. If the ledger changes: the mention review (6.2), its marker holding `m`.
3. The reply with its marker. Only this makes the mention answered.

A run can die or be cancelled by a push between any two steps. The next run finds the
mention unanswered and looks for the receipts first:

- a bot review whose `m.k` is the key: the model is not run again. Step 3 is done from `m`:
  "이전 실행에서 반영했습니다." with the summary and the link of that review, which holds
  the answer.
- else a commit on the PR branch with the trailer: the session runs again, and step 1 is
  skipped; the guideline is reported from that commit (`g`), whatever the model proposes
  this time.
- else: the mention is handled from the start.

A failed step leaves the mention unanswered for the next run and makes the exit code 1.
The next run comes with the next event of the PR (a push or a mention); on an idle PR the
mention waits until then. A cancellation (the runner's signal) stops the run at once: it is
never caught as an error to continue after.
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
- `op` is the command's (5.4). `update`/`remove`: `target` is a rule id in that file;
  `remove` of the last rule deletes it.
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
- After the bot's commit the author's local branch is behind: their next push needs a pull
  first. The reply says so ("브랜치에 커밋을 추가했습니다. 다음 push 전에 pull 하세요.").
- The PAT reaches git only through `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_0`/`GIT_CONFIG_VALUE_0`
  (`http.<GHES origin>/.extraheader`, so no other host gets it), never argv, `.git/config`
  or the model's environment. These variables need git >= 2.31: an older git is reported as
  a failed commit ("git 2.31 이상이 필요합니다"), and the README lists the requirement.
- The commit sets author and committer to the bot (a runner has no git identity). The push
  URL is the PR head repository's clone URL. `GIT_TRACE*` and `GIT_CURL_VERBOSE` are removed
  from git's environment; git's error text goes to the log only, the reply gets a fixed
  Korean sentence.
- A push that reports an error is checked before it counts as failed: when the branch tip
  on the remote carries this mention's trailer, the push landed.
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
| `review/run.py` | The two phases of one run (3) and the exit code |
| `review/mention/{cli,fetch,command,context,apply,body}.py` | The mention phase |
| `review/mention/ledger.py` | Applying a mention's changes to a state, `ledger_event` |
| `review/rules.py`, `review/rules_git.py` | Guidelines (7) |
| `agent/pr-mention.md`, `config/mention-schema.json` | Agent and schema |

Changed: `__main__.py` (calls `run`), `cli.py` (review phase callable), `render.py` (issue
marker, `parse_reply`), `validate.py` (`approvable`, `unclear`, `m`), `scope.py` and
`aggregate.py` (write `approvable` and `unclear`; no recheck of every issue when only uncovered files changed), `claude_cmd.py`/`claude_runner.py`
(session kinds), `prompt.py` (guideline section), `github_api.py` (comments, replies, one
retry), defaults, template, README, runbook.

## 10. Testing

- Unit: command parsing (slash commands only: `철회 안 해도 돼`, `철회`, `/철회?` and
  `/철휘` are no command; quoted and code text; ids next to Korean and in inline code;
  the single open issue as target); mention detection (`@ai-bot님`, `mention_since`,
  permission fallback); applying changes to a state (waive, add, upgrade, nothing else touched, size
  fit); `ledger_event` (blocking, `unclear`, `approvable` false, not current, an old marker with
  and without a blocker added by the mention);
  `approvable` and `unclear` written by `merge`/`next_state` and carried by `settle_state`;
  checks of 5.4 (targets only, already waived, link upgrade 60 -> 95, hunk range, an
  excluded file, no state, a guideline `op` other than the command's, state not at the head
  with the same and with a different blob); `determine` on a head that differs only in
  excluded files (no recheck, `unchanged`) and in a covered file (recheck as today); rules (format, ids, overflow,
  indexes, select order); rules_git (PAT, trailer, git version, a push that landed but reported an error, semantic
  retry with a concurrent edit of the same file, final failure cleanup); context (`LEFT` from the merge base, outdated,
  multi-line); forged markers posted by a person and echoed in a bot reply.
- Integration (temp repos, fake `claude`, fake GitHub, bare remote):
  - `@ai-bot /철회` on the only blocking issue -> APPROVE, change requests dismissed;
  - `@ai-bot 철회 안 해도 돼, 진짜 버그야` -> conversation, nothing waived;
  - the same on a review that had no targets, or with a cut file -> COMMENT, not APPROVE;
  - `@ai-bot 이건 의도된 거야` (no command) -> reply only;
  - a question whose injected model output withdraws an issue -> nothing withdrawn;
  - new issue on a diff line -> REQUEST_CHANGES; outside the hunks -> note only;
  - `@ai-bot /지침 ...` -> commit pushed with the trailer; no model review follows; a
    later `/철회` of the only blocker approves; the next review's prompt contains the rule;
  - `@ai-bot /지침 ...` while the model answers with `op: remove` -> nothing committed;
  - a mention on a PR without any state (draft never reviewed) -> a reply, no crash;
  - a mention created before `mention_since` -> ignored;
  - the head moved after checkout -> the phase stops, the mention stays pending;
  - a comment event on a head never reviewed -> the review is posted first, then the reply,
    and a new issue from the mention lands in the state of that review's head;
  - an incomplete state with an unrelated blocking orphan + a withdrawal -> the orphan stays
    open and blocking;
  - recovery, one test per gap: dead after the push (session runs, no second commit), after
    the mention review (no session, one reply linking the review), a guideline removal
    after the push; a receipt found when the time is almost up (the reply is the receipt's,
    not the time message);
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

- Withdrawal and guideline need a slash command; a natural-language request without it
  gets an answer and a hint.
- With a guideline command the model picks the rule to change or remove; a wrong pick is
  visible in the reply and in the PR diff and is reverted by a person.
- A change in an excluded file is not taken as a reason to recheck the open issues.
- A person who dismisses a mention review waives every open issue listed in it, as with any
  bot review.
- A mention review can make the marker so large that `fit_state` gives up part of the
  seen-set; those files are reviewed again on the next push.
- A mention made while a review runs is answered after that review (up to the review
  deadline in the worst case); a push during a mention's handling cancels it and the
  push's run answers it after its review.
- A comment event on a PR whose last review failed starts that review again before it
  answers.
- `issue_comment` handling follows the default branch's workflow file.
- Guidelines reach other open PRs after they merge or rebase main.
- A guideline commit is never reviewed by the AI (its directory is excluded); people see it
  in the PR diff.
- An edit of an answered comment is not read; an edit before the answer is.
- A mention whose run died is answered at the PR's next event, not before.

## 12. Out of Scope

- Reacting without a mention; reopening a waived issue through a mention; guidelines shared
  across repositories.
