# Convergence Dedupe Holes Implementation Plan (rev 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** No real defect that the model reports is folded into a known issue without a person seeing it, and the fixes create no new path that keeps a review from converging.

**Architecture:** A finding folded into an open issue by position is shown in that run's review body instead of being hidden, so no new ledger state is needed. A finding more severe than the issue its `same_as` names becomes its own issue, and the ledger remembers which issue it came from, so later reports naming that issue merge into it. A recheck that a chunk limit kept from running is served first on the next run.

**Tech Stack:** Python 3 standard library, `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-02-review-convergence-design.md` (rev 7). This plan makes it rev 8.

## History

- Rev 1 kept the lines of a folded finding out of the seen data. Codex: a same-sha retry under `max_chunks` never progresses; one run in which the model does not repeat the finding releases the hold; a substitute `same_as` link keeps no finding text.
- Rev 2 stored a folded finding as a hidden `held` issue, rechecked and posted when its parent closed. Codex: a distinct defect near a held issue is folded again by position, one level down; `fit_state` can blank the text of a held issue nobody has seen; promotion chains post the same problem under a new id on every push; a repeated more-severe `same_as` outside `NEAR` blocks every time.
- Rev 3 follows Codex's simpler suggestion: show the folded finding at once.
- Rev 3.1, after a third Codex review: `_dedupe` removes a finding before `classify` sees it, so it is folded too; the body reserves space for the folded section; `via` lookup and storage use one source id and the `same_as` distance counts from the nearest issue of that source; a recheck line cut for length no longer counts as shown.

## Global Constraints

- Rev 7 rules stay unless a task changes them: closure only on shown lines, a closed issue never reopens, severity and blocking frozen at the first judgment, `NEAR` 3, `SAME_AS` 15, a position match is a new issue only when strictly more severe than every nearby issue.
- Seen data (`seen`, `seen_files`, `seen_hunks`) is computed exactly as today.
- No change may create a same-sha run that never stops, or an issue raised again on every push.
- Code and docs in English; text shown in a PR comment or the log is Korean.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. The issue a finding was folded into is resolved in the same run: the finding was still shown in that run's body.
2. A distinct defect next to another folded finding, including one removed earlier by `aggregate._dedupe`: each is listed on its own. Tests start at `merge` or the CLI, not at `classify`.
3. A body whose state marker is near its budget and whose previous-status section is long still shows the folded section.
4. The model names a closed issue in `same_as` for the same critical problem on lines that drift by 4 each push, also through an intermediate issue of the same source: at most one new issue per 15 lines of drift, and none while the reports stay within 15 lines of an issue of that source.
5. A recheck-only file split by `max_chunks`, and a recheck item with a line too long to show: neither ends in a wrong `resolved`, and neither keeps a same-sha re-run from stopping.

---

### Task 1: Show a folded finding in the body

**Files:**
- Modify: `review/aggregate.py` (`_dedupe`, `merge`), `review/ledger.py` (`classify`), `review/render.py` (`_sections`, `render_body`), `review/cli.py` (log)
- Test: `tests/test_aggregate.py`, `tests/test_ledger.py`, `tests/test_render.py`, `tests/test_convergence.py`

**Interfaces:**
- Two sources of folded findings:
  - `classify` marks a duplicate merged by position into an open issue with `folded: True`, `dup_of` the issue id. A duplicate of a closed issue named by `same_as` and a `same_as` link to an open issue are not folded: the model said it is the same problem.
  - `_dedupe` returns `(kept, removed)`. A removed finding on a different line from the one kept, or with a different title, is folded with `dup_of` set to `"이번 실행 <file>:<line>"` of the kept one. One on the same line with the same title is a copy from overlapping chunks and stays dropped (logged).
- `merge` returns all folded findings in `review["folded"]`.
- `render._sections` adds a section `### 기존 이슈에 합쳐진 지적 (n건)`. Each entry: ``- `file:line` <severity label> <title> → <이슈 id or 이번 실행 위치>: <first 200 characters of detail>``. At most 20 entries, most severe first, then `외 m건 (로그 참조)`. The counter `기존 이슈와 중복되어 생략 n건` counts only duplicates that are not folded.
- `render_body` reserves the folded section's space before it cuts anything: when the body is over `MAX_BODY`, the previous-status section (`### 이전 지적 처리 현황`) is shortened first (entries replaced by `외 n건`), and the folded section is cut last, keeping at least its header, the first entry and the `외 m건 (로그 참조)` line.
- The log lists every folded finding with its full detail.
- Nothing is stored in the state, and the seen data is unchanged.

- [ ] **Step 1: Write the failing tests**
  - `test_a_position_merge_into_an_open_issue_is_folded` and `test_same_as_duplicates_and_links_are_not_folded`.
  - `test_folded_findings_are_listed_in_the_body`: open major A at 50, major B at 52 -> the body lists B with its title, severity and detail excerpt, and `→ 이슈 <A id>`; no inline comment for B; the event is the one without B.
  - `test_folded_is_shown_when_its_issue_resolves_in_the_same_run` (Focus 1).
  - `test_two_distinct_folded_findings_are_both_listed` (Focus 2): through `merge`: A at 50 open; B at 52 and C at 53 with the same severity, confidence and category -> B and C both listed, one via `classify`, one via `_dedupe`.
  - `test_an_overlap_copy_is_not_folded`: the same finding from two chunks is dropped and not listed.
  - `test_the_folded_list_is_capped`: 30 folded findings -> 20 entries and `외 10건`.
  - `test_folded_section_survives_a_full_body` (Focus 3): 160 previous issues with 80-character titles and 100-character notes, and a state marker of about 39,900 characters -> the body is within `MAX_BODY` and still holds the folded header, its first entry and the `로그 참조` line.
- [ ] **Step 2: Run them to verify they fail.** `python3 -m unittest tests.test_ledger tests.test_render tests.test_convergence`
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Full suite** `python3 -m unittest discover -s tests`, OK.
- [ ] **Step 5: Commit**: `feat: show a finding folded into an open issue in the review body`

Accepted cost: a reworded re-raise of an open issue is listed again each time it is reported, which is each push that changes those lines. A person who reads the list once and moves on will not see that finding again.

### Task 2: `same_as` does not swallow a more severe finding, and remembers its source

Hole: resolved minor C at 100; a critical D at 110 is reported with `same_as: C`. It is dropped today. With an open C, it is a link and keeps C's minor severity.

**Files:**
- Modify: `review/ledger.py` (`classify`, `new_issue`), `review/validate.py` (`_issue`, `compact_issue`, `expand_issue`), `review/scope.py` (`_record`)
- Test: `tests/test_ledger.py`, `tests/test_validate.py`, `tests/test_convergence.py`

**Interfaces:**
- An issue may carry `via: "<issue id>"`: the issue that a more severe `same_as` named when this issue was created. It is identity data. A compact closed record gains an optional sixth field, `<id>:<line>:<lo>:<hi>:<s><v>:<via>`; `expand_issue` accepts 5 or 6 fields.
- `source(I) = I.via or I.id`. The family of an issue T is every non-orphan issue in T's file, open or closed, with `source == source(T)`; T is in its own family.
- A `same_as` naming T is valid when T is an orphan (as today), or when the finding is in T's file and within `SAME_AS` lines of the nearest member of T's family. Today only T's own region counts. The distance limit stays because a wrong `same_as` must not hide a defect far from every issue of that family.
- `classify`, for a finding with a valid `same_as` naming T:
  1. Let M be the most severe member of T's family, nearest first on ties. If M is at least as severe as the finding: treated as if `same_as` named M (link for open M, drop for closed M). With a family of one, this is today's rule.
  2. Otherwise: the `same_as` is ignored (log: `same_as 무시: 지정한 이슈보다 심각함`), the finding goes through the position rules, and if it becomes a new issue it gets `via = source(T)`. So the new issue joins T's family and both lookup and storage use the same source id.

- [ ] **Step 1: Write the failing tests**
  - `test_same_as_to_a_less_severe_closed_issue_does_not_drop`: D is a new issue with `via == C.id`, REQUEST_CHANGES end to end.
  - `test_same_as_to_a_less_severe_open_issue_is_not_a_link`.
  - `test_same_as_at_least_as_severe_still_merges`.
  - `test_a_repeated_more_severe_same_as_converges` (Focus 4): the model names C (minor, resolved, line 100) for a critical problem at 102, 106, 110, 114, 117 over five runs. Run 1 creates X (via C) at 102. Every later run links to X or, once X is resolved after run 2, is dropped: each report is within 15 lines of X. No new issue and no REQUEST_CHANGES after run 2.
  - `test_drift_beyond_the_family_creates_one_issue_per_15_lines`: same, continuing at 118 and 122: 118 is 16 lines from X and 18 from C, so it creates X2 (via C) once; 122 is within 15 of X2 and is merged or dropped.
  - `test_same_as_naming_an_intermediate_issue` (Codex chain): minor C 100, major T 110 (via C), critical V 120 (via C), T and V resolved; a critical at 124 with `same_as: T` is dropped as V, with no new issue.
  - `test_via_survives_compaction`: a closed issue with `via` round-trips through `compact_issue` / `expand_issue`; a 5-field record still expands.
- [ ] **Step 2: Run them to verify they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Full suite**, OK.
- [ ] **Step 5: Commit**: `fix: do not let same_as hide a more severe finding`

Accepted costs: a report that drifts more than 15 lines from every issue of its family creates one new issue each time it does. A marker written by this version can lose `via` if read by an older version; the older version then drops that closed record.

### Task 3: A recheck kept back by the chunk limit is served next

Reproduced (script kept outside the repo): `src/r.cu`, already fully reviewed, has open issues at lines 10 and 190; a later head touches only another file. With `chunk_max_tokens` 50 and `max_chunks` 1, `_recheck` makes the hunks nearest both issues a target, `build_chunks` keeps the first hunk and puts the file in `skipped`. Then:
- `aggregate._previous` refuses `resolved` for both issues because the file is skipped, although both recheck items were in the chunk (`assign_recheck` gives every item of the file to the first chunk holding it, with its own lines);
- `pending_after` puts both ids back;
- every same-sha re-run and every later head that does not touch the file repeats this: `recheck_pending` never empties, `complete` stays false, a blocking issue blocks forever.
On 346cb1d (the PR's current head) the ids are forgotten instead (`recheck_pending` empty, later runs `unchanged`), so the issues also never close.

Root cause: a file that is a target only for rechecks (no line of it in `changed`) is judged by the rule meant for a file whose new lines were not all reviewed. For a recheck-only file the per-issue rule already decides: an item's lines are in `shown`, or they are not.

**Files:**
- Modify: `review/scope.py` (`_recheck`: mark its targets `recheck_only: True`), `review/cli.py` (pass the recheck-only paths on), `review/aggregate.py` (`_previous`, `merge`), `review/ledger.py` (`pending_after`)
- Test: `tests/test_aggregate_prev.py`, `tests/test_ledger.py`, `tests/test_convergence.py`

**Interfaces:**
- `merge(..., recheck_only: Optional[Set[str]] = None)` and `_previous(..., recheck_only=())`: for a path in `recheck_only`, being in `skipped` or `truncated` does not refuse `resolved`; the `shown` rule and "every chunk holding the file is ok" still apply.
- `pending_after(..., recheck_only=())`: for such a path, an id is pending when it was left out, or when any chunk holding the file is not `ok`. This is exactly the condition under which `_previous` refuses `resolved` for it, so an id is never both refused and dropped from pending.
- `complete` and the verdict: a run whose only skipped or truncated files are recheck-only, and in which every recheck item was served by an `ok` chunk, is complete. Otherwise as today.
- Cut lines count as not shown (Codex found that `shown_lines` counted a line cut at 500 characters as fully seen, so a defect past the cut could be closed). In `ledger._window`, a line inside the issue's region is cut at `REGION_LINE_MAX` = 4000 characters instead of `LINE_MAX` = 500 (context lines keep 500). `shown_lines` leaves out every line that was cut, so an issue whose region has a cut line can never be `resolved` from this item. Such an id is not put in `recheck_pending`, because no later run can show more of the line; the log says `줄이 너무 길어 재검토 불가: <id>`. The issue stays open until the code changes or a person dismisses it.

- [ ] **Step 1: Write the failing tests**
  - `test_recheck_only_file_split_by_max_chunks_progresses` (Focus 5): the reproduction above as an end-to-end test; run 2 rechecks both issues, accepts `resolved` for both, `recheck_pending` is empty, the event is APPROVE.
  - `test_a_skipped_file_with_new_lines_still_refuses_resolved`: same setup, but the skipped hunk has new lines: today's behaviour.
  - `test_recheck_only_item_left_out_stays_pending`: with a `recheck_max_chars` too small for the second item, only that id is pending, and the next run serves it first.
  - `test_recheck_only_file_with_a_failed_chunk_keeps_its_ids_pending`: the file is split into two chunks that both run; the one holding the recheck items is `ok`, the other fails. `resolved` is refused and the ids stay pending; a same-sha re-run rechecks them.
  - `test_a_defect_past_a_cut_is_not_resolved` (Codex case): the reproduction above with the line-190 defect past character 4000 of a 6000-character line; the model reports both issues resolved -> the line-190 issue stays open, REQUEST_CHANGES stays, its id is not pending, and a same-sha re-run stops as `unchanged`.
  - `test_a_region_line_under_4000_is_shown_whole`: a 3000-character region line is not cut, and `resolved` is accepted.
- [ ] **Step 2: Run them to verify they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Full suite**, OK.
- [ ] **Step 5: Commit**: `fix: let a recheck-only file split by the chunk limit converge`

Accepted costs: in a recheck-only file, a skipped hunk is not shown to the model; that hunk has no new lines, so nothing in it is a review target anyway. An issue on a line longer than 4000 characters is never closed by a recheck; it needs a code change or a dismissal. A region line of up to 4000 characters makes one recheck item up to 11 × 4000 characters, over `recheck_max_chars`; the existing "first item of a chunk is served even when over budget" rule applies.

### Task 4: Docs

- [ ] Spec rev 8: the folded list (Task 1), `via` (Task 2), the recheck order (Task 3), each with its accepted cost, and the History section above. Update `docs/runbook.md` for the new body section and log lines.
- [ ] Commit: `docs: convergence design rev 8`

## Alternatives considered

- **Hidden held issues (rev 2).** Rejected; see History.
- **Ask the model per pair.** After the review, resume the session once and ask "same problem or not?" for each finding near a known issue without `same_as`. No new state, but one more model turn per such chunk (calls already take minutes), and a wrong "same" still drops the defect. It could be added later to shorten the folded list.
- **Do only Task 2.** Leaves a same-severity defect folded into an open issue unseen. Rejected.
