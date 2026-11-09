# Review Convergence: Issue Ledger

Status: draft for review, rev 9 (after four Codex adversarial reviews, the final branch
review, a Codex review of PR #10, three Codex reviews of the dedupe-holes plan and its final
branch review, and a Codex adversarial review of rev 8). Extends
`2026-10-18-ai-code-review-design.md` (state marker, sections 6 and 8).

## Problem

Fixing every finding and pushing again still yields "new" findings. The dominant kind is the
same issue raised again in other words, on a neighboring or rewritten line. Causes in the
current code:

1. A re-raised issue is a new finding: severity and confidence are judged from scratch, and
   a re-raise that is now blocking is explicitly allowed (`aggregate.merge`).
2. `finding_id` hashes file + anchor + title; the anchor is the fingerprint of exactly one
   line. A changed title, a neighboring line, or a rewritten line makes a new issue.
3. The reviewer sees previous findings only as id/file/line/title/severity; no description,
   and nothing about resolved ones.
4. Resolved findings leave the state, and a dismissed review is read back as live state, so
   there is no memory that an issue was closed.

## Goal

Repeated reviews of one PR converge. An issue is raised once; once closed it never comes
back. What remains are genuinely new issues in newly changed lines. No round limit (decided:
not now).

## Design

### Issue identity: a tracked region, not wording or line content

Rev 1 matched issues by line-content fingerprints. Codex showed both failure directions:
common lines (`}`, request parsing) merged distant distinct defects, and a rename plus a
line shift lost the issue. Rev 2 tracks position through git instead.

- Each ledger issue has a file, a primary line and a region `[lo, hi]` of head lines; a new
  issue starts as `[line, line]`.
- Every run maps each region from the previous state's sha to the new head with
  `git diff -U0 -M <prev sha> <head>` (the same diff `renames_between` already uses; the
  checkout has `fetch-depth: 0`):
  - a line outside every hunk shifts by the hunks above it;
  - a line inside a hunk's old range (rewritten or deleted) maps to that hunk's new range,
    clipped to 5 lines on each side of the proportional position (a rewrite of a large
    block does not swallow the whole block). An end of the region inside a hunk with old and
    new lines takes that hunk's new start or end before the clipping, so a one-line region
    widens through a rewrite (rev 6); a pure insertion never widens a region;
  - a hunk with no new lines maps to the single line where it was.
  The primary line is mapped the same way. After mapping, the region is cut to at most 5 lines
  on each side of the primary line (rev 5), so code inserted inside a region cannot widen it:
  `[95, 105]` with 1,000 lines inserted after line 100 does not become `[95, 1105]`.
  The mapped region is stored, so it is followed across any number of pushes.
- Moves (rev 5): `-M` follows file renames only. Code moved inside or across files shows up as
  a deletion hunk plus an insertion hunk, and the status never follows it automatically. Rev 3
  and rev 4 tried to detect moves by similarity, and each rule had a counterexample that passed
  a waiver to unrelated code, such as the same block on a different resource.
  An open issue whose whole region falls in lines deleted by a hunk with no new lines is first
  looked for in its own file (rev 6): the region's lines at the previous sha are searched as
  contiguous lines in the head file, compared after `fingerprint.norm` (trailing whitespace
  stripped, the seen-set's normalization, rev 7), and the hit nearest the old line wins. When
  the whole region is not found, the 3-line window of the primary line (`fp_line`, as anchors)
  is searched instead, nearest hit first (rev 7), so a move that also edits one region line is
  still followed. A hit relocates the issue (region shifted by the same offset as the primary
  line, clipped to the head file); it stays open and is rechecked as usual. The reason: a move inside one file is not re-reviewed by the seen-set.
  The moved lines keep their fingerprints, so they are seen and not in `changed`, the model
  cannot report them, and without this search a reorder of functions would close a blocking
  issue unseen. Raw comparison missed a move that added trailing spaces: the lines were seen
  under the normalization, the issue became an orphan and a complete run resolved it unfixed
  (rev 7). Accepted cost: an open issue can relocate to an unrelated identical line or 3-line
  window, and the model is asked once more.
  Without a hit by either search, and always for a closed issue, the issue becomes an orphan: it has no region,
  and only the model's `same_as` can attach a finding to it (see below). Closed issues are
  never searched, so a waiver does not transfer (rev 6). Accepted cost: if the model does not
  link moved code, the same issue can be raised once more. That fails toward showing a defect,
  not hiding one. The other side of the seen-set rule applies here too: code with a closed
  orphan that moved inside one file is seen, so it is not reviewed again.
- A new finding matches an issue when it is in the same file and its line is within 3 lines
  of the issue's region. A finding farther away is a separate issue, however similar its
  text or code (Codex finding 1). An orphan has no region, so position never matches it.
  Principle (rev 9): a blocking finding is merged into, swallowed by, or made non-blocking by
  a known issue only when the model named that issue in `same_as` (the reach rules below).
  Position only annotates a blocking finding; the position rules still apply to non-blocking
  findings, which cannot change the verdict. One exception needs no `same_as`: a finding,
  blocking or not, without a valid `same_as` merge that is on the same file and line as a
  non-orphan open issue, has the same title and is not more severe is an exact re-report and
  a link to that issue, handled like a `same_as` link (it overrides a same-run resolved report
  and a blocking one makes the issue blocking). A link keeps an open issue open, so it cannot
  lower the verdict; a closed issue never gets this match. Let `near` be the non-orphan
  issues of the file within 3 lines, open or closed (an open issue reported resolved in the
  same run counts as open):
  - a blocking finding is always a new issue and keeps its blocking. When `near` is not empty
    it names the nearest issue in it (an open one on ties): `near_issue` for an open one (log
    `기존 이슈 근처의 새 지적`), `near_closed` for a resolved or waived one (the comment says
    `해결/면제된 이슈 <id> 근처의 새 지적` and the log lists it);
  - a non-blocking finding strictly more severe than every issue in `near` is a new issue
    with `near_issue` naming the nearest one;
  - otherwise, with an open issue in `near`, a non-blocking finding is merged into the nearest
    open one (folded, below);
  - otherwise (only closed issues near) it is a new issue with `near_closed` naming the
    nearest one; it keeps its `needs_check`.
  The reason: every rule that dropped, folded or un-blocked a blocking-grade finding by
  position alone had a wrong-approval path (rev 7 and 8 hid a distinct critical next to a
  resolved critical, a distinct major next to an open major, and a defect folded into an
  issue the model closed). A finding that is shown once more is an acceptable cost; a hidden
  defect or a wrong approval is not. Accepted cost: a blocking re-raise of a known problem
  that the model words differently (another title or line) without `same_as` becomes a new
  issue and blocks again (an exact re-report of an open issue converges as a link),
  also next to a waived issue (the prompt asks for `same_as`; a waiver of the new issue
  sticks like any other).
- The model may also link a finding to a ledger issue by giving its id in a new optional
  `same_as` field. The link is honored only in the same file and within 15 lines of the
  region, so a wrong link cannot hide a defect elsewhere. This covers rewrites that move the
  code more than 3 lines (Codex finding 2). For an orphan issue (above) the distance limit
  does not apply: the code it pointed at no longer exists, so the link cannot hide a defect
  in code the issue still covers. A link gives the orphan a region at the finding's line, and
  the log lists each such link.
- `same_as` never hides a more severe finding (rev 8). An issue made by a more severe
  `same_as` carries `via`, the id of the issue that was named (identity data; a compact closed
  record gains an optional sixth field `:<via>`, and a record with 5 fields still reads).
  `source(I)` is `I.via` or `I.id`. The family of an issue T is T and every non-orphan issue,
  open or closed, with the same source in T's file. A `same_as` naming T is valid for an
  orphan T (the family is then T and the non-orphan issues of that source in the finding's
  file, with T at infinite distance), or when the finding is in T's file within 15 lines of the
  nearest member of the family. M is chosen only within reach of the finding: the candidates
  are T itself when it is an orphan or within 15 lines of the finding, and the family members
  within 15 lines of the finding. M is T when T is a candidate at least as severe as the
  finding; else M is the most severe candidate (nearest first on ties). If M is at least as
  severe as the finding, the finding is treated as if `same_as` named M: a link for an open M,
  a drop for a closed M. Otherwise the `same_as` is ignored (log `same_as 무시: 지정한 이슈보다
  심각함`), the finding goes through the position rules, and if it becomes a new issue it gets
  `via = source(T)` and so joins the family. A far member never takes the report (final branch
  review): it sent a `same_as` to an open orphan to an unrelated critical 200 lines away, so the
  orphan was auto-resolved instead of relinked and the run approved, and it swallowed a finding
  more severe than every issue near it.
  A `same_as` naming T in T's file but beyond 15 lines from every member is not valid, yet a
  finding at least as severe as every member still gets `via` when it becomes a new issue: a
  report that drifts a few lines each push then lands in the family and converges. Repeated
  reports within 15 lines of a family member are linked or dropped; a report that drifts more
  than 15 lines from every member creates one new issue, and the next ones stay near it.
  Accepted costs: such a drift creates one new issue each time it passes 15 lines (also when a
  report naming an orphan drifts 15 lines past the issue its first report made); a wrong far
  `same_as` can put an unrelated issue into a family, so that a later report naming the family
  merges into it (the merge still needs a member at least as severe within 15 lines); a marker
  written by this version and read by an older one loses `via`, and the older version drops
  that closed record.
- Fallback when the previous sha is not available (force-push to a commit never fetched):
  locate by the stored anchor print as today (`previous.relocate`); if that fails, keep the
  old region. Only this case weakens the guarantee; the run log says so.
- Title and category are not part of identity, so a reworded or re-categorized report still
  matches through `same_as`, and a non-blocking one also by position. Accepted cost: a second,
  different non-blocking issue within 3 lines of an open issue, and not more severe than every
  issue near it, is folded for good (listed once; its line is seen, so it is not reviewed
  again). A reworded re-raise near a closed issue without `same_as` is posted again, blocking
  when it is blocking-grade (rev 9).
- A finding merged into an open issue by position is shown (rev 8). Earlier revisions dropped
  it and only counted it, so a distinct defect of the same severity within 3 lines of an open
  issue was never seen by a person. Such a finding is `folded`: `classify` marks a duplicate
  merged by position into an open issue, and `aggregate._dedupe` marks a finding it removed
  that is on another line than the one it kept, or has another title (`dup_of` is then
  `이번 실행 <file>:<line>`). A `same_as` drop or link is not folded: the model said it is the
  same problem. A removed finding on the same line with the same title is a copy from
  overlapping chunks; it stays dropped and the log lists it (`겹치는 조각의 같은 지적 생략`).
  Nothing is stored in the state and the seen data is unchanged. Since rev 9 a fold only holds
  a non-blocking finding, so it never changes the verdict. `blocking` is set before
  `_dedupe`, which removes a blocking finding only as a copy of a kept one (same file, line,
  title and `same_as`; the stronger is kept, the copy is not folded), never as a near
  duplicate. A non-blocking finding near a kept blocking one (same file and category, within
  2 lines) is folded into it, so it is never kept over it; two near non-blocking findings keep
  the stronger, as before. Accepted cost: a reworded non-blocking re-raise of an open issue is
  listed again on each push that changes those lines; a person who reads the list once and
  moves on will not see that finding again.

### Ledger and closure

- Each issue has a status: `open`, `resolved` or `waived`. `resolved` and `waived` are
  closed, and a closed issue never reopens, whatever the new severity (decided).
- A valid `same_as` link is open evidence (rev 9). When the model reports an open issue
  resolved in this run and also links a finding to it (in another chunk, or through a family
  member), the link wins: the issue stays open (log `같은 문제가 다시 지적되어 해결 보고를
  무시`), and a blocking link makes it blocking (stored so). An issue of a file that left the PR
  stays resolved. This happens before orphans are settled and before the verdict; the issue
  was not closed yet, so it is not a reopening of a closed issue. An exact re-report link
  counts the same. A finding `_dedupe` removed (folded or an overlap copy) is also passed
  through `classify`, and only its links are kept, so a weaker finding carrying the `same_as`
  next to a stronger one without it is still evidence (review M1: otherwise the resolved
  report stood and the run approved). A `same_as` ignored because the finding is more severe
  than every issue in reach is evidence for the named issue too: it reopens an issue reported
  resolved in this run (logged the same way) but never changes its blocking, and it is not a
  link (no orphan moves).
- Issue ids are assigned once and never recomputed.
- Severity and confidence are frozen at the first judgment, and so is blocking, except that a
  blocking link to a non-blocking open issue makes it blocking (rev 9, above); nothing makes
  an issue non-blocking.
- Principle (rev 3): an issue is never closed on lines the model was not shown. Closure is
  permanent, so a wrong closure cannot be undone; when in doubt the issue stays open.
  `resolved` is accepted only when all of the following hold, otherwise the issue stays
  `open` (or `unclear`, as today):
  - the file is in neither the skipped nor the truncated list of this run;
  - every chunk that holds the file is `ok` (as today in `aggregate._previous`);
  - the issue's region lies inside the lines shown to the model in one of those chunks.
  An issue whose file left the PR or was deleted is still resolved automatically, as today,
  and so is an issue of a PR file without any hunk (rev 7, see Recheck).
- Recheck (rev 4): a fix can leave the flagged line untouched, for example a null check added
  above it or a change in a caller. The incremental scope would then never show the region,
  and the rule above would keep a fixed issue open forever. So on every run whose head differs
  from the previous state's sha, every open issue is rechecked, whether or not its file has
  new lines:
  - the issue's file becomes a target, with the hunks nearest the region as `_recheck` does
    today for lost findings, and the chunk's prompt carries the head lines of the region
    with 20 lines on each side, marked as recheck context;
  - these lines count as shown for closure, but they are not added to `changed`, so new
    findings there are still dropped ("incremental reviews flag only newly changed lines");
  - recheck context counts toward the chunk size. The first item of a chunk that does not fit
    the budget gets less context, down to its region alone, and is served even when the
    region alone is over the budget (rev 7), so an item is never too large for every run.
    Before the budget check, a recheck line longer than 500 characters is cut to 500 and
    marked ` …(줄 잘림)` (line numbers unchanged; since rev 8 a line of the issue's region is cut
    at 4,000, see below), so one item is bounded: a region has at most
    11 lines. A
    later item left out because of size, or one whose chunk is not `ok`, goes into the
    state's `recheck_pending` list of ids. So does an issue whose file is held by any chunk
    that is not `ok` (rev 6), or is in the skipped or truncated list of this run (rev 7),
    because a `resolved` report is refused then. Pending items are served first on the next
    run, so they always make progress. A state with a non-empty list never stops early as
    `unchanged` on the same sha. The list does not change the verdict: an open blocking issue
    still blocks.
  - an issue of a file with no hunks in the PR diff (a pure rename, or a change the PR
    reverted) cannot be shown in any chunk. Such a file is no longer changed by the PR, so its
    open issues are resolved automatically like those of a file that left the PR, with the
    note `PR 변경이 되돌려짐` (rev 7; rev 6 kept them pending, which never converged). A binary
    file is not such a file.
  - a file that is a target only for rechecks (none of its lines is in `changed`; `scope`
    marks it `recheck_only`) is judged by the per-item rule alone (rev 8). Being in the skipped
    or truncated list (for example a hunk dropped by `max_chunks`) no longer refuses `resolved`
    for it, and it does not make the run incomplete: the item shows the issue's own lines, and
    those lines must be in the shown set, in a chunk that is `ok`, with every chunk holding the
    file `ok`. The pending rule is the same condition, so an id is never both refused and not
    pending. If any recheck item was left out, a skipped or truncated recheck-only file counts
    as a gap again. Before rev 8 such a file kept its ids pending on every run, and `complete`
    never became true. Accepted cost: a skipped hunk of a recheck-only file is not shown to the
    model; it has no new lines, so nothing in it is a review target anyway.
  - a line is counted as shown only when the model saw all of it (rev 8). A recheck line inside
    the issue's region is cut at 4,000 characters (context lines still at 500), and a cut line
    is not in the shown set, so an issue whose region has a cut line can never be `resolved` by
    its recheck: a defect past the cut could otherwise be closed unseen. The cut alone does not
    put the id in `recheck_pending`, because no later run can show more of the line; it is
    still pending when it was left out or a chunk holding its file is not `ok` (the rules
    above). When it is not pending, the log says `줄이 너무 길어 재검토 불가: <id>`. The cut item counts as served for `complete` (the run did
    all it can for it), and the issue stays open, and blocking if it was, until the code changes
    or a person dismisses it. Accepted costs: an issue on a line over 4,000 characters is never
    closed by a recheck; a region line of up to 4,000 characters makes one recheck item up to
    11 x 4,000 characters, over `recheck_max_chars`, and the existing rule applies (the first
    item of a chunk is served even when over budget).
- An open orphan is resolved automatically when the run is complete. Since rev 6 an open issue
  becomes an orphan only when its lines are no longer in its file, so its code is gone or was
  changed; code moved to another file is on new lines there and gets reported there. A
  `same_as` link in that run comes first and keeps the issue open at its new region. On an
  incomplete run it stays open. A closed orphan stays closed and stays in the ledger, so a later
  link can still match it.
- A new finding linked to an open issue by `same_as`, or a non-blocking one near it, is merged
  into it (no new comment). One that names a closed issue in `same_as` is dropped; a blocking
  one without `same_as`, a non-blocking one more severe than every issue near it, or one near
  only closed issues, is kept (rev 9, above). The body shows "기존 이슈와 중복되어 생략 n건"
  for the dropped ones only (those not folded); the log lists each one.
- Folded section (rev 8). The body gets `### 기존 이슈에 합쳐진 지적 (n건)`: one entry per
  folded finding, ``- `file:line` <severity> <title> → 이슈 <id> | 이번 실행 <file>:<line>: <first
  200 characters of detail>``, most severe first, at most 20, then `외 m건 (로그 참조)`. The log
  lists every folded finding with its full detail (`기존 이슈에 합쳐진 지적`). A folded finding
  gets no inline comment and never blocks; it is never blocking-grade (rev 9, above).
  When the body is over its 60,000-character limit, space goes in this order: the previous
  status section is shortened first (its entries become `외 n건`); then, among the rest, the
  sections before the late ones, new findings without an inline comment included; then the
  folded section's minimum (its header, its first entry and the `외 m건 (로그 참조)` line);
  then more folded entries; the chunk summaries, error causes and scope notes last. So a new
  finding is never cut for a folded one, and a long previous list never hides the folded
  section. Accepted cost: on a very full body fewer folded entries are shown; all are in the log.

### Dismissal is a waiver (Codex finding 3)

- Every run lists all bot reviews, not only the latest. For each review in state
  `DISMISSED`, the dismisser comes from the PR's `review_dismissed` issue events. A
  dismissal by anyone other than the bot (the bot dismisses its own reviews in
  `failure.py`) waives every issue that review's marker lists as open.
- Ids are stable, so a dismissal of any earlier review applies to the current ledger,
  including one made while a run was in progress: the next run picks it up. Applying it
  again is a no-op.
- A run that waives at least one issue never stops early as `unchanged` (rev 3). Today
  `scope.determine` returns `unchanged` when the state's sha is the head and the review was
  complete, and `cli._review` then posts nothing, so a `REQUEST_CHANGES` posted during the
  race would stay. Instead, such a run posts a review with the updated ledger and a verdict
  from the remaining issues (no model call when there are no new targets): `APPROVE` when no
  blocking issue is left, which replaces the bot's earlier change request on GitHub.

### Reviewer context

- The prompt lists the ledger issues for the chunk's files, open and closed: id, region,
  severity, status, title and the first 200 characters of the description, with the rules
  "do not report these again in `findings`", "if a finding is the same problem as a
  listed issue, set `same_as` to its id" and "a different problem near a listed issue,
  resolved and waived ones included, is a new finding without `same_as`" (rev 7).
- Every chunk also lists all orphan issues of the PR, whatever their file (rev 5), because moved
  code usually lands in a file other than the issue's: id, severity, status and title, at most
  4000 characters per chunk, newest first. Orphans left out are named in the log.
- The prompt only saves effort; convergence is enforced by the matching rules above.

### State (Codex finding 4)

- Marker v3. Issue records are split into identity and display:
  - identity, which is always kept: id, file, region, status, severity, blocking, anchor.
    Closed issues are stored per file in a compact form, about 20 characters each. A compact
    closed record holds id, file, line, region, status and severity, with no anchor or blocking
    (rev 6). `recheck_pending` is identity data too.
  - display, which can be dropped: title and description excerpt.
- `fit_state` order when over budget (rev 6), until the marker fits:
  1. move seen to whole-file hashes;
  2. compact the closed issues;
  3. blank title and excerpt of open non-blocking issues, oldest first;
  4. blank title and excerpt of open blocking issues, oldest first;
  5. drop seen, seen files and seen hunks; the display text that then fits is kept;
  6. drop the oldest compact closed records and set `dropped_closed`.
  Seen goes late because an empty seen-set re-reviews the whole PR, the failure this design
  prevents; display text only costs readability. Open issues and `recheck_pending` are never
  removed.
- With seen dropped, the 40,000-character budget holds about 1,900 issue records. Beyond
  that, step 6 applies and the body says so; this is the only path that loses closure memory.
- A v2 marker is migrated on first read: each finding becomes an open issue with region
  `[line, line]` at the v2 sha.
- `cut` (rev 9, optional): per file, the packed prints of changed lines too long for a chunk
  (see "A line longer than a chunk" below). It is small and `fit_state` never trims it.

### A line longer than a chunk (rev 9)

- `hunks.split_hunk` makes one oversized row its own sub-hunk. Before rev 9, `diff_split._units`
  cut that unit at the last newline before `MARKER`, which dropped the row: it never reached
  an `ok` chunk, never entered the seen-set, the file was `truncated` on every run, and every
  same-sha re-run repeated the same state (Codex adversarial review).
- Now a unit over the limit whose hunk has one content row (plus an optional `\ No newline`
  row) keeps the row with its text cut to fit: the `+`/`-`/` ` prefix stays and
  ` …[ai-review: 줄이 너무 길어 잘림]` marks the cut. Such a unit is not `MARKER`-truncated and
  the file is not `truncated` for it. A multi-row unit over the limit keeps the `MARKER` cut
  (it makes progress across runs). Each chunk has `cut: {path: [new-side lines]}` and, when
  that is not empty, `full_diff`, the diff with those rows whole.
- The cut row counts as seen: prints and hunk keys are made from `full_diff` (an added line's
  print comes from the blob by line number; a removed line's print needs its whole text). A
  same-sha re-run with nothing else new is `unchanged`.
- The cut row is not shown for resolving issues: `ledger.shown_lines` leaves the chunk's `cut`
  lines out of the diff's lines (a recheck item that shows the line whole still counts).
- The cut line is a permanent gap that never approves while it is in the head. The state
  keeps the prints of the cut changed rows of `ok` chunks in `cut`; `next_state` keeps the
  previous ones still in the head prints (renames followed like the seen-set) and adds this
  run's; `settle_state` carries them. `determine` sets `cut_files`; the CLI passes their union
  with the files of this run's chunk `cut` (any chunk) to `merge`, which turns `APPROVE` into
  `COMMENT` and names them (`줄이 너무 길어 일부만 검토한 파일`). `complete` is not changed, so
  orphan auto-resolution and same-sha stopping still work, and nothing blocks. The gap goes
  when the line leaves the head or its print changes (the line or a neighbour is edited, so
  the new content is reviewed again). Why never approve: the model saw only the start of the
  line, so an approval would vouch for code nobody read; a re-review cannot show more, so
  keeping the line unseen would repeat the run forever. A person reviews the line by hand, or
  excludes generated/minified files with `exclude_globs`.

### Unchanged

The blocking rule (critical/major with confidence >= 80), COMMENT on incomplete runs, and
"incremental reviews flag only newly changed lines" all stay.

## Tests

Fake-model scenarios:

- a reworded re-raise on a neighboring line is suppressed;
- a re-raise after resolution named by `same_as` is suppressed, even as critical;
- severity stays at the first judgment;
- a v2 to v3 migration keeps the open issues;
- the ledger reaches the prompt, and `same_as` within 15 lines links, beyond 15 does not.

Regression tests for the Codex scenarios:

- two handlers 40 lines apart sharing 4 parsing lines: an issue in each stays separate;
- a waived issue whose code gets a local-variable rename and 15 lines inserted above is
  still matched and stays waived;
- a dismissal made between the state read and the post is applied on the next run;
- 200 closed issues in one file all survive `fit_state`, with seen data present;
- region mapping: shift, rewrite inside a hunk, deletion, large-block clipping, rename.

Regression tests for the second Codex review (rev 3):

- a file split over chunks where the chunk holding the issue is skipped, or the file is
  truncated: a `resolved` report is rejected and the issue stays open; the retry can still
  report it;
- a `resolved` report from an ok chunk that did not show the issue's region is rejected;
- a dismissal during a run, then a re-run on the same sha: the run is not `unchanged`, posts
  `APPROVE` when nothing blocking is left, and makes no model call;
- an orphan issue (code deleted): `same_as` far from the old region is
  honored; a `same_as` beyond 15 lines for a non-orphan issue is not.

Regression tests for the third Codex review (rev 4):

- a null check added above an open issue, with the flagged line unchanged: the region is shown
  as recheck context, `resolved` is accepted, and a new finding on a recheck line is dropped;
- a fix in another file, with no new lines in the issue's file: the issue is still rechecked;
- recheck context that does not fit: the id is in `recheck_pending`, the verdict still blocks,
  and a same-sha re-run is not `unchanged`;

Regression tests for the fourth Codex review (rev 5):

- 1,000 lines inserted inside a closed region: the region stays within 5 lines of the primary
  line, and a new finding in the inserted code is kept;
- a waived 5-line block deleted and the same block on another variable added in another file:
  the waiver does not move, and a new finding on the new lines is kept;
- a function with a waived issue moved 40+ lines: without `same_as` the finding is new, with
  `same_as` it is dropped and the issue gets a region at the finding's line;
- a short block moved to another file, in a different chunk: the destination chunk's prompt
  lists the orphan, and `same_as` links it;
- an open orphan: resolved on a complete run, kept open on an incomplete run, and kept open at
  the new region when linked.

Regression tests for the final review (rev 6):

- a function with a blocking defect reordered inside its file, model silent:
  `REQUEST_CHANGES`, and the issue stays open at the new line;
- the same move of a waived issue: it stays a waived orphan;
- the same move with the defect line changed: an orphan, resolved on a complete run;
- a one-line region rewritten into 11 lines: the region is 6 lines, and a reworded re-raise on
  the rewritten block is not new;
- 160 open blocking issues with long text fit the budget, and a state over budget keeps its seen
  data until blank display text is not enough;
- a file split over two chunks, the chunk with the recheck `ok` and saying `resolved`, the other
  not `ok`: the id is in `recheck_pending`, and a same-sha re-run resolves the issue;
- an open issue in a file with no hunks in the PR diff: the id is in `recheck_pending`, the log
  names it, and the run is not `unchanged` (replaced in rev 7, below).

Regression tests for the Codex review of PR #10 (rev 7; the non-blocking and merge outcomes
for blocking-grade findings are replaced in rev 9, below):

- a resolved minor issue at line 100 and a new critical finding on line 102 without `same_as`:
  the finding is kept and blocks (`REQUEST_CHANGES`); one of the same severity is posted, does
  not block, and is marked as near the closed issue; `same_as` to the closed issue drops it; a
  waived issue behaves the same; an open issue nearby still wins when not less severe;
- an open minor issue reported resolved in the same run as a critical finding two lines away:
  the finding is new and blocks (`REQUEST_CHANGES`); a same-severity re-raise near an open issue
  still merges; a resolved critical at 100 and a resolved minor at 103 with a new critical at
  101: posted near the closed critical without blocking, and a re-raise next to it merges into
  it; a waived critical at 100 and an open minor at 103: a critical re-raise at 101 never
  blocks over three runs with a dismissal between them;
- a blocking issue's function moved inside its file with trailing spaces added: the issue stays
  open and is rechecked (`REQUEST_CHANGES`); a moved region with one line edited but the primary
  window intact relocates; a region and window that are gone make an orphan;
- a file split over two chunks with one dropped by `max_chunks`, the recheck saying `resolved`:
  the id is pending and the same-sha re-run rechecks and resolves it; a truncated file likewise
  keeps the id pending;
- an open blocking issue in a file the PR no longer changes (revert plus rename): resolved with
  `PR 변경이 되돌려짐`, nothing pending, `APPROVE`, and the same-sha re-run is `unchanged`;
- a recheck item over `recheck_max_chars` is served with less context; a region alone over the
  budget is served alone; a region on a 300 KB line is served with the line cut, and the chunk
  prompt stays small.

Regression tests for the dedupe-holes plan (rev 8; since rev 9 the folded findings are
non-blocking ones):

- an open issue at 50 and a non-blocking finding at 52: the body lists the second with its
  title, severity, detail excerpt and `→ 이슈 <id>`, with no inline comment; a `same_as` drop
  or link and an overlap copy are not listed;
- an open major orphan T, a critical X at 300 made with `same_as: T`, and a major at 105 with
  `same_as: T` while X is resolved (or open): T is relinked to 105 and `REQUEST_CHANGES`; a
  resolved minor C at 100, a critical X at 300 with `via = C`, and a major at 105 with
  `same_as: C`: a new issue, not a drop into X; T within reach and as severe as the finding
  wins over a more severe member next to it;
- an `APPROVE` whose only skipped or truncated file is recheck-only, with an open non-blocking
  issue there: the reason is `차단 사유 없음`, not a skipped file (`merge` returns `gaps`);
- two distinct findings next to one open issue, one folded by `classify` and one removed by
  `_dedupe`: both are listed; 30 folded findings give 20 entries and `외 10건`;
- a body whose state marker is near its budget and whose previous list is long: the folded
  section keeps its header, first entry and `로그 참조` line, and new findings without an
  inline comment are not cut for it;
- a resolved minor C with a critical `same_as: C` ten lines away: a new issue with `via`, and
  `REQUEST_CHANGES`; a `same_as` to an equal or more severe issue still merges; a critical
  reported with `same_as: C` at lines drifting by 4 per push creates no new issue while within
  15 lines of the family, and one issue per 15 lines beyond; a `same_as` naming an
  intermediate issue of the family is dropped as the most severe member; `via` survives
  `compact_issue` / `expand_issue`, and a 5-field record still expands; a `same_as` to an
  orphan in another file converges;
- a recheck-only file split by `max_chunks` with two open issues: both rechecked and resolved,
  nothing pending, `APPROVE`; a skipped file with new lines still refuses `resolved`; a left-out
  item stays pending and is served first on the next run; a failed chunk keeps its ids pending;
- a defect past a cut on a 6,000-character region line: the issue stays open, `REQUEST_CHANGES`
  stays, the id is not pending and a same-sha re-run is `unchanged`; a 3,000-character region
  line is shown whole and `resolved` is accepted.

Regression tests for the Codex adversarial review of rev 8 (rev 9):

- an open major A at 50 and a distinct major/95 B at 52 without `same_as`: B is a new
  blocking issue, not folded, and stored as its own open issue; a next run where A is
  reported resolved and B is not reported is `REQUEST_CHANGES`;
- a resolved critical C at 50, a critical/99 A at 60 with `same_as: C` and a critical/95 B at
  62 without it: A is dropped, B is new and blocks;
- `_dedupe`: two blocking findings with the same file, line, title and `same_as` keep one and
  list the other as an overlap copy; the same line with another `same_as` keeps both; a
  non-blocking critical/70 next to a blocking major/95 of the same category is folded into it;
- a resolved minor C at 100 and an open critical X at 110 (`via` C); chunk 0 reports X
  resolved, chunk 1 reports a major at 115 with `same_as: C` linking to X: X stays open and
  the run is `REQUEST_CHANGES`; a non-blocking X linked from a blocking finding becomes
  blocking; end to end, the log names the overridden report;
- a resolved critical at 100 and a new major/95 at 102 without `same_as`: new, blocking,
  `near_closed`, `REQUEST_CHANGES`; a waived critical with a critical re-raise next to it
  blocks without `same_as` and never blocks with it;
- a non-blocking finding near an open issue is still folded and listed in the body; one near
  only a closed issue is new with `near_closed`; a blocking finding near an open issue whose
  `same_as` names it is still a link;
- review M1: an open blocking major X at 100 reported resolved, a minor/60 at 102 with
  `same_as: X` and a minor/90 at 101 of the same category: X stays open, `REQUEST_CHANGES`;
  the same with the `same_as` finding folded into a blocking finding at 103, and with an exact
  re-report in place of the `same_as`: X stays open; a critical whose `same_as` to a resolved
  minor X is ignored: X stays open and non-blocking;
- an exact re-report (same file, line and title, not more severe) of an open blocking issue:
  a link, no new finding, `REQUEST_CHANGES`, and it overrides a same-run resolved report; the
  same line with another title, or more severe, is new and blocks; the same line and title as
  a resolved issue is not a link (normal rules).

Regression tests for a line longer than a chunk (rev 9):

- `chunk_max_tokens` 400, one new line of about 4 KB and an open issue elsewhere in the file:
  the run is complete, the line is seen, the file is not `truncated`, the body names it under
  `줄이 너무 길어 일부만 검토한 파일`, the event is `COMMENT`, and a same-sha re-run is
  `unchanged`; a later push of other lines with every chunk ok and no findings is still
  `COMMENT`; once the line is shortened, `APPROVE`; a rename keeps the gap;
- an issue on a cut row is not resolved by a `resolved` report (a recheck that shows the line
  whole still counts);
- a multi-row unit over the limit is still `MARKER`-truncated and `truncated`;
- a removed long row's print comes from its whole text; `cut` survives `normalize_state`,
  `fit_state` under a tight budget, and a settle run.

## History

- Rev 1 of the dedupe-holes plan kept the lines of a folded finding out of the seen data. Codex:
  a same-sha retry under `max_chunks` never progresses; one run in which the model does not
  repeat the finding releases the hold.
- Rev 2 stored a folded finding as a hidden `held` issue, rechecked and posted when its parent
  closed. Codex: a distinct defect near a held issue is folded again one level down; `fit_state`
  can blank a held issue nobody has seen; promotion chains post the same problem under a new id
  on every push.
- Rev 3 shows the folded finding at once, which needs no new state. Rev 3.1 (third Codex
  review): `_dedupe` removes a finding before `classify` sees it, so it is folded too; the body
  reserves space for the folded section; `via` lookup and storage use one source id; a recheck
  line cut for length does not count as shown.
- Rev 8 of this spec is the result: the folded section, `via`, and the recheck-only rule with
  the cut region line. Hidden held issues were rejected, and so was asking the model per pair
  ("same problem or not?"), which costs one more model turn per chunk and still drops a defect
  on a wrong "same". That could be added later to shorten the folded list.
- The final branch review of rev 8 found two wrong-approval paths, fixed in rev 8: a `same_as`
  merged into the family's most severe member at any distance (an orphan relink went to a far
  issue; a far member swallowed a more severe finding), so M is now chosen within 15 lines of
  the finding and T wins when at least as severe; and a folded finding never counted toward
  the verdict, so a fold into an issue resolved in the same run, or into a less severe one, was
  promoted (removed in rev 9: a blocking finding is no longer folded). Costs: a report drifting more than 15 lines from every member makes one new
  issue (as before for non-orphans); a rewording reported next to its own resolved issue is
  raised once and may block once. The verdict reason now names only the gaps that count.
- Rev 9 (Codex adversarial review of rev 8): four wrong-approval paths came from rules that
  used position only to drop, fold or un-block a blocking-grade finding: a distinct major
  folded into an open major that the next run resolved, a near-duplicate removed by
  `_dedupe` next to a `same_as` drop, a link losing to a resolved report in the same run, and
  a major next to a resolved critical posted without blocking. Rev 9 lets only `same_as`
  merge or un-block a blocking finding, removes fold promotion, sets `blocking` before
  `_dedupe`, and lets a link win over a resolved report of the same run. An exact re-report
  of an open issue is a link. Cost: a blocking re-raise reworded without `same_as` is a new
  issue and blocks again.
  Rev 9 also cuts a line longer than a chunk instead of dropping it (a same-sha fixed point
  in the Codex review): the line is seen, and a permanent `cut` gap keeps it from approval.
  Cost: a PR with such a line never gets `APPROVE` from the bot until the line changes or
  leaves the PR; a very small `chunk_max_tokens` can make ordinary lines cut rows too.

## Order

PR #5 has landed the earlier pending branches. This work rewrites `aggregate.py`, `prompt.py`,
`scope.py` and the state code.
