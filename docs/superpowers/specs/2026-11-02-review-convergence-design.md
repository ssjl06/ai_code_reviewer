# Review Convergence: Issue Ledger

Status: draft for review, rev 2 (after a Codex adversarial review). Extends
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

- Each ledger issue has a file and a region `[lo, hi]` of head lines; a new issue starts as
  `[line, line]`.
- Every run maps each region from the previous state's sha to the new head with
  `git diff -U0 -M <prev sha> <head>` (the same diff `renames_between` already uses; the
  checkout has `fetch-depth: 0`):
  - a line outside every hunk shifts by the hunks above it;
  - a line inside a hunk's old range (rewritten or deleted) maps to that hunk's new range,
    clipped to 5 lines on each side of the proportional position (a rewrite of a large
    block does not swallow the whole block);
  - a hunk with no new lines maps to the single line where it was.
  The mapped region is stored, so it is followed across any number of pushes.
- A new finding matches an issue when it is in the same file and its line is within 3 lines
  of the issue's region. A finding farther away is a separate issue, however similar its
  text or code (Codex finding 1).
- The model may also link a finding to a ledger issue by giving its id in a new optional
  `same_as` field. The link is honored only in the same file and within 15 lines of the
  region, so a wrong link cannot hide a defect elsewhere. This covers rewrites that move the
  code more than 3 lines (Codex finding 2).
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

### Reviewer context

- The prompt lists the ledger issues for the chunk's files, open and closed: id, region,
  severity, status, title and the first 200 characters of the description, with the rules
  "do not report these again in `findings`" and "if a finding is the same problem as a
  listed issue, set `same_as` to its id".
- The prompt only saves effort; convergence is enforced by the matching rules above.

### State (Codex finding 4)

- Marker v3. Issue records are split into identity and display:
  - identity, which is always kept: id, file, region, status, severity, blocking, anchor.
    Closed issues are stored per file in a compact form, about 20 characters each.
  - display, which can be dropped: title and description excerpt.
- `fit_state` order when over budget: compact seen into hashes (as today), drop seen and
  seen hunks (costs a re-review, not correctness), drop display text of closed issues, then
  of open non-blocking issues. Identity records are never dropped by size.
- With seen dropped, the 40,000-character budget holds about 1,900 issue records. Beyond
  that, the oldest closed records are dropped and the body says so; this is the only path
  that loses closure memory.
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

## Order

Land the pending branches first (`fix/korean-output`, `chore/max-chunks-ceiling`,
`fix/budget-stop`). This work rewrites `aggregate.py`, `prompt.py` and the state code they
also touch.
