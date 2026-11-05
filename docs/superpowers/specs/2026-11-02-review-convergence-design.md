# Review Convergence: Issue Ledger

Status: draft for review, rev 6 (after four Codex adversarial reviews and the final branch
review). Extends
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
  raw contiguous lines in the head file, and the hit nearest the old line wins. A hit relocates
  the issue (region to the hit, primary line at the same offset); it stays open and is
  rechecked as usual. The reason: a move inside one file is not re-reviewed by the seen-set.
  The moved lines keep their fingerprints, so they are seen and not in `changed`, the model
  cannot report them, and without this search a reorder of functions would close a blocking
  issue unseen. Accepted cost: an open issue can relocate to an unrelated identical line, and
  the model is asked once more.
  Without a hit, and always for a closed issue, the issue becomes an orphan: it has no region,
  and only the model's `same_as` can attach a finding to it (see below). Closed issues are
  never searched, so a waiver does not transfer (rev 6). Accepted cost: if the model does not
  link moved code, the same issue can be raised once more. That fails toward showing a defect,
  not hiding one. The other side of the seen-set rule applies here too: code with a closed
  orphan that moved inside one file is seen, so it is not reviewed again.
- A new finding matches an issue when it is in the same file and its line is within 3 lines
  of the issue's region. A finding farther away is a separate issue, however similar its
  text or code (Codex finding 1). An orphan has no region, so position never matches it.
- The model may also link a finding to a ledger issue by giving its id in a new optional
  `same_as` field. The link is honored only in the same file and within 15 lines of the
  region, so a wrong link cannot hide a defect elsewhere. This covers rewrites that move the
  code more than 3 lines (Codex finding 2). For an orphan issue (above) the distance limit
  does not apply: the code it pointed at no longer exists, so the link cannot hide a defect
  in code the issue still covers. A link gives the orphan a region at the finding's line, and
  the log lists each such link.
- Fallback when the previous sha is not available (force-push to a commit never fetched):
  locate by the stored anchor print as today (`previous.relocate`); if that fails, keep the
  old region. Only this case weakens the guarantee; the run log says so.
- Title and category are not part of identity, so a reworded or re-categorized report still
  matches. Accepted cost: a second, different issue within 3 lines of a known one is
  suppressed.

### Ledger and closure

- Each issue has a status: `open`, `resolved` or `waived`. `resolved` and `waived` are
  closed, and a closed issue never reopens, whatever the new severity (decided).
- Issue ids are assigned once and never recomputed.
- Severity, confidence and blocking are frozen at the first judgment.
- Principle (rev 3): an issue is never closed on lines the model was not shown. Closure is
  permanent, so a wrong closure cannot be undone; when in doubt the issue stays open.
  `resolved` is accepted only when all of the following hold, otherwise the issue stays
  `open` (or `unclear`, as today):
  - the file is in neither the skipped nor the truncated list of this run;
  - every chunk that holds the file is `ok` (as today in `aggregate._previous`);
  - the issue's region lies inside the lines shown to the model in one of those chunks.
  An issue whose file left the PR or was deleted is still resolved automatically, as today.
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
  - recheck context counts toward the chunk size. An issue left out because of size, or
    whose chunk is not `ok`, goes into the state's `recheck_pending` list of ids. So does an
    issue whose file is held by any chunk that is not `ok`, because a `resolved` report is
    refused then (rev 6), and an issue of a file with no hunks in the PR diff (a pure rename),
    which no chunk can hold; the log names it (rev 6). A state with a non-empty list never
    stops early as `unchanged` on the same sha. The list does not change the verdict: an open
    blocking issue still blocks.
- An open orphan is resolved automatically when the run is complete. Since rev 6 an open issue
  becomes an orphan only when its lines are no longer in its file, so its code is gone or was
  changed; code moved to another file is on new lines there and gets reported there. A
  `same_as` link in that run comes first and keeps the issue open at its new region. On an
  incomplete run it stays open. A closed orphan stays closed and stays in the ledger, so a later
  link can still match it.
- A new finding that matches an open issue is merged into it (no new comment). One that
  matches a closed issue is dropped. The body shows "기존 이슈와 중복되어 생략 n건"; the
  log lists each one.

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
  "do not report these again in `findings`" and "if a finding is the same problem as a
  listed issue, set `same_as` to its id".
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

### Unchanged

The blocking rule (critical/major with confidence >= 80), COMMENT on incomplete runs, and
"incremental reviews flag only newly changed lines" all stay.

## Tests

Fake-model scenarios:

- a reworded re-raise on a neighboring line is suppressed;
- a re-raise after resolution is suppressed, even as critical;
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
  names it, and the run is not `unchanged`.

## Order

PR #5 has landed the earlier pending branches. This work rewrites `aggregate.py`, `prompt.py`,
`scope.py` and the state code.
