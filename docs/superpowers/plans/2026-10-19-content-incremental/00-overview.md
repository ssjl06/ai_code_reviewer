# Content-Based Incremental Review Implementation Plan — Overview

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make follow-up reviews survive squash, rebase and force push by detecting
changes from line content (fingerprints) instead of commit ancestry, and stop the
verdict from approving work that was not fully reviewed.

**Architecture:** `scope.determine` always diffs `base...head`, fingerprints every
added/removed line against head file contents, and keeps only hunks containing
lines whose fingerprint count rose above the stored multiset. The state marker
(v2) stores per-file fingerprint multisets and per-finding anchors. Previous
findings are relocated by anchor. The verdict refuses APPROVE when chunks are
partial/incomplete, files were skipped, or a previous blocking finding is unclear.

**Tech Stack:** Python 3.9+ standard library only, `unittest`, git.

**Spec:** `docs/superpowers/specs/2026-10-18-ai-code-review-design.md` (rev 2:
sections 6, 7.1, 8, 11). Previous plan (already implemented):
`docs/superpowers/plans/2026-10-18-ai-code-review/`.

## Plan Files

| File | Tasks |
|---|---|
| `00-overview.md` | this file |
| `01-verdict.md` | Task 1 verdict fixes: `partial` chunks, skipped files, unclear blockers |
| `02-fingerprint.md` | Task 2 `review/fingerprint.py`, Task 3 `review/hunks.py` + `gitutil.read_blobs` |
| `03-previous.md` | Task 4 `review/previous.py` + anchor ids in `aggregate.py` |
| `04-scope.md` | Task 5 scope v2, `review/seen.py`, state v2 |
| `05-integration.md` | Task 6 prompt/agent/cli wiring, force-push integration tests, runbook |

Execute in order. Every task leaves the full suite green.

## Global Constraints

- Python standard library only. Target Python 3.9 (no `match`, no runtime `X | Y`).
- Each source/test file ~150 lines, hard stop ~180 (`review/diff_split.py` is at 172: add nothing net to it).
- English code/comments/docs; Korean for agent text, prompts and PR-facing strings (exact strings below).
- Full suite: `python3 -m unittest discover -s tests` must pass after every task.
- Commit message ends with a blank line then `Co-Authored-By: <implementing model> <noreply@anthropic.com>`.
- Spec values: fingerprint = first 4 bytes of sha1; seen cap 24,000 characters; lines normalized by
  `rstrip()` only (leading indentation kept).

## Review Focus

1. CRLF files — `\r` in diff lines and blob lines must not change fingerprints (Task 2 `test_crlf_same_print`, Task 3 `test_crlf_blob_matches_diff`).
2. Corrupted `seen` base64 in a marker — that file is treated as never reviewed, no exception (Task 2 `test_unpack_corrupt_is_empty`).
3. `\ No newline at end of file` lines inside hunks — ignored, line numbers stay right (Task 3 `test_no_newline_marker_ignored`).
4. Previous finding in a renamed file — carried to the new path, not auto-resolved (Task 4 `test_rename_carries_finding`).
5. Oversized hunk truncated with `[truncated by ai-review]` — only visible lines count as reviewed; the cut tail stays new (Task 5 `test_truncated_tail_stays_new`).

## Shared Data Shapes (new or changed)

```python
Print = bytes                      # 4-byte fingerprint
Change = {"kind": "+" | "-", "line": int, "fp": Print}
    # "+": head line number of the added line
    # "-": anchor line of the deletion point (head line just below it, or the last line at EOF)
Seen = Dict[str, collections.Counter]          # path -> multiset of Print

FileSelection = {"file": FileDiff | None,      # FileDiff with only the kept hunks
                 "added": Set[int], "deleted": Set[int],   # new "+" lines / new deletion anchors
                 "head": Counter}              # all "+"/"-" prints of the file at head

ChunkResult.status: "ok" | "partial" | "incomplete" | "failed"
    # partial = valid output with incomplete_reason set (Task 1)

StateV2 = {"v": 2, "sha": str, "complete": bool,
           "seen": {path: base64 str}, "seen_files": {path: base64 str},
           "findings": [{"id", "file", "line", "anchor", "title", "severity", "blocking"}]}
    # anchor = 8 hex chars (Print.hex()); "" when unknown

PrevEntry = StateV2 finding + {"pre": "resolved" | "open" | "ask", "located": bool,
                               "note": str (optional)}           # produced by previous.relocate

Review (adds): "complete": bool, "skipped": list[str], "unclear_blocking": bool

Scope = {"mode": "full" | "incremental" | "unchanged", "range": str,
         "targets": list[FileDiff], "pr_files": list[FileDiff], "reviewable": int,
         "changed": Dict[str, Set[int]] | None, "added": Dict[str, Set[int]],
         "deleted": Dict[str, Set[int]], "blobs": Dict[str, List[str]],
         "head_prints": Seen, "prev_seen": Seen, "previous": list[PrevEntry]}
```

## Korean strings (exact)

| Where | Text |
|---|---|
| verdict reason, unclear blocker | `이전 차단 지적 확인 필요` |
| verdict reason, skipped files | `생략된 파일 있음 (조각 수 한도)` |
| partial chunk error | `미완료 사유: <incomplete_reason>` |
| auto-resolved note | `파일이 PR에서 빠짐` |
| unchanged log | `새로 리뷰할 변경 없음 — 리뷰를 건너뜀` |
| prompt mode, incremental | `증분 리뷰: 이전 리뷰 이후 새로 바뀐 줄만 리뷰한다.` |
| prompt headers | `검토 대상 줄:` and `삭제 지점:` |
