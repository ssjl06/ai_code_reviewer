# Review Convergence: Issue Ledger

Status: draft for review. Extends `2026-10-18-ai-code-review-design.md` (state marker, sections 6 and 8).

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

### Issue identity: location, not wording

- When an issue is first raised, store a window: line fingerprints of the flagged line and
  3 lines on each side (7 prints, 4 bytes each).
- A new finding matches a ledger issue in the same file (renames followed as today) when
  either holds:
  - its line falls inside the issue's window, relocated to the head by the existing anchor
    relocation (`previous.relocate`);
  - its own 7-line window shares at least 2 prints with the stored window.
- Title and category are not part of identity, so a reworded or re-categorized report still
  matches. Accepted cost: a second, different issue right next to a known one is suppressed.

### Ledger and closure

- Each issue has a status: `open`, `resolved` or `waived`. `resolved` and `waived` are
  closed, and a closed issue never reopens, whatever the new severity (decided).
- Severity, confidence and blocking are frozen at the first judgment.
- A new finding that matches an open issue is merged into it (no new comment). One that
  matches a closed issue is dropped. The body shows "기존 이슈와 중복되어 생략 n건"; the
  log lists each one.
- Dismissal is a waiver: when a non-bot user dismissed the bot review that holds the latest
  state, every issue open in that review becomes `waived`.

### Reviewer context

- The prompt lists the ledger issues for the chunk's files, open and closed: id, line,
  severity, status, title and the first 200 characters of the description, with the rule
  "do not report these again in `findings`".
- The prompt only saves effort; convergence is enforced by the matching rules above.

### State

- Marker v3: each issue adds `status`, `window` (base64, about 40 characters) and the sha
  where it closed.
- `fit_state` drops the oldest non-blocking closed issues first when over budget; only very
  large PRs lose closure memory.
- A v2 marker is migrated on first read: windows are computed from the stored anchors at
  the previous head.

### Unchanged

The blocking rule (critical/major with confidence >= 80), COMMENT on incomplete runs, and
"incremental reviews flag only newly changed lines" all stay.

## Tests

Fake-model scenarios:

- a reworded re-raise on a neighboring line is suppressed;
- a re-raise after resolution is suppressed, even as critical;
- after a dismissal and a push, the issue is not raised again;
- severity stays at the first judgment;
- a v2 to v3 migration keeps the open issues;
- the ledger reaches the prompt.

## Order

Land the pending branches first (`fix/korean-output`, `chore/max-chunks-ceiling`,
`fix/budget-stop`). This work rewrites `aggregate.py`, `prompt.py` and the state code they
also touch.
