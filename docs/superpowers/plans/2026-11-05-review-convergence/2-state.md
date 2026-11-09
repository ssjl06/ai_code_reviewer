# Review Convergence Implementation Plan (2 of 3: closure and state)

Header, Global Constraints and Review Focus are in `1-ledger.md`, and they apply here.

---

### Task 4: Closure rules

**Files:**
- Modify: `review/aggregate.py`, `review/ledger.py`
- Test: `tests/test_aggregate_prev.py`, `tests/test_ledger.py`

**Interfaces:**
- Consumes: `classify` links from Task 3
- Produces:
  - `merge(..., shown: Optional[Dict[int, Dict[str, Set[int]]]] = None)`: chunk index -> file -> lines shown to the model. `None` skips the region check, so existing callers keep working until Task 6.
  - `ledger.shown_lines(chunks: List[dict], recheck: Dict[int, List[dict]]) -> Dict[int, Dict[str, Set[int]]]`: the `right_lines` of each chunk diff, plus the numbered lines of its recheck items
  - `ledger.finish_orphans(prev_out: List[dict], links: List[dict], complete: bool) -> None`: updates entries in place
  - A previous entry with `pre == "orphan"` is an open orphan.

- [ ] **Step 1: Write the failing tests**

```python
def test_resolved_needs_the_region_in_shown_lines(self):
    out = run_merge(resolved_report("i1"), issue("a.cc", 50, id="i1"),
                    shown={0: {"a.cc": {1, 2, 3}}})
    self.assertEqual(status(out, "i1"), "open")
def test_resolved_is_accepted_when_the_region_was_shown(self):
    out = run_merge(resolved_report("i1"), issue("a.cc", 50, id="i1"),
                    shown={0: {"a.cc": {49, 50, 51}}})
    self.assertEqual(status(out, "i1"), "resolved")
def test_resolved_is_rejected_for_a_skipped_file(self):
    out = run_merge(resolved_report("i1"), issue("a.cc", 50, id="i1"), skipped=["a.cc"],
                    shown={0: {"a.cc": {50}}})
    self.assertEqual(status(out, "i1"), "open")
def test_resolved_is_rejected_for_a_truncated_file(self):
    out = run_merge(resolved_report("i1"), issue("a.cc", 50, id="i1"), truncated=["a.cc"],
                    shown={0: {"a.cc": {50}}})
    self.assertEqual(status(out, "i1"), "open")
def test_resolved_needs_every_chunk_holding_the_file_ok(self):
    out = run_two_chunks(ok_status="ok", other_status="partial")
    self.assertEqual(status(out, "i1"), "open")
def test_open_orphan_resolves_on_a_complete_run(self):
    self.assertEqual(status(run_merge([], orphan_prev("i1")), "i1"), "resolved")
def test_open_orphan_stays_open_on_an_incomplete_run(self):
    self.assertEqual(status(run_merge(failed_chunk(), orphan_prev("i1")), "i1"), "open")
def test_linked_orphan_stays_open_at_the_finding(self):
    out = run_merge(link_report("i1", "b.cc", 500), orphan_prev("i1"))
    p = entry(out, "i1")
    self.assertEqual((p["status"], p["file"], p["line"], p.get("orphan")),
                     ("open", "b.cc", 500, False))
def test_all_chunks_failed_closes_nothing(self):
    out = run_merge(all_failed(), issue("a.cc", 50, id="i1"), shown={})
    self.assertEqual(status(out, "i1"), "open")
    self.assertEqual(out["event"], "REQUEST_CHANGES")
```

The last test uses a blocking issue. It pins Review Focus 4. `tests/test_ledger.py` covers `shown_lines`: a diff's right lines plus recheck lines, per chunk.

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_aggregate_prev tests.test_ledger`
Expected: failures on the new tests (`shown` is an unknown argument).

- [ ] **Step 3: Implement.** In `aggregate._previous`, add three conditions to the existing `resolved` gate: the file is not in `skipped`, the file is not in `truncated`, and `range(lo, hi + 1)` (with `line` as the default for both until Task 5) lies inside `shown[i][path]` for a chunk `i` that holds the file. `merge` computes `complete` before it calls `finish_orphans`, and `finish_orphans` runs before `blocking` is computed. A link sets `file`, `line`, `lo`, `hi` and `orphan: False`, and it wins over auto-resolution.

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m unittest tests.test_aggregate tests.test_aggregate_prev tests.test_aggregate_anchor tests.test_ledger`
Expected: OK

- [ ] **Step 5: Commit**: `feat: close an issue only on lines the model was shown`

### Task 5: State v3

**Files:**
- Modify: `review/validate.py`, `review/scope.py`, `review/state_fit.py`, `review/previous.py`, `review/render.py`
- Test: `tests/test_validate.py`, `tests/test_state_fit.py`, `tests/test_scope_state.py`, `tests/test_previous.py`, `tests/test_scope_moved.py`, `tests/test_scope_regress.py`, `tests/test_scope.py`

**Interfaces:**
- Consumes: `ledger.advance`, `ledger.from_v2`, `ledger.new_issue`, `gitutil.diff_u0`, `regions.parse_u0`
- Produces:
  - State v3: `{"v": 3, "sha", "complete", "seen", "seen_files", "seen_hunks", "issues": [...], "closed": {file: "<compact> <compact>"}, "recheck_pending": [ids]}`
  - Compact closed record: `<id>:<line>:<lo>:<hi>:<s><v>`, where `s` is `r` or `w` and `v` is the first letter of the severity; an orphan has `lo` and `hi` of `0`
  - `normalize_state(state) -> Optional[dict]`: accepts v2 (migrated with `from_v2`) and v3, always returns v3 with `closed` expanded into `issues`
  - `determine(...)` output gains `closed` (closed issues after `advance`) and `weak_tracking` (bool). `previous` holds open issues only, as entries with `pre` and `located`.
  - `next_state(head, review, scope, chunks, results)` writes v3.
  - `previous.relocate` is removed; `_closest`, `GONE_NOTE`, `DELETED_NOTE` and `anchor_of` stay.

- [ ] **Step 1: Write the failing tests**

```python
def test_v2_marker_migrates_open_issues(self):
    st = normalize_state(V2_STATE)
    self.assertEqual(st["v"], 3)
    self.assertEqual([(i["id"], i["status"], i["lo"], i["hi"]) for i in st["issues"]],
                     [("abcd1234", "open", 10, 10)])
    self.assertEqual(st["seen"], V2_STATE["seen"])
def test_v1_state_means_no_state(self):
    self.assertIsNone(normalize_state({"v": 1}))
def test_compact_closed_round_trips(self):
    st = normalize_state(dict(V3_STATE, closed={"a.cc": "abcd1234:10:9:11:wm"}))
    i = st["issues"][-1]
    self.assertEqual((i["id"], i["status"], i["severity"], i["lo"]),
                     ("abcd1234", "waived", "major", 9))
def test_a_malformed_issue_is_dropped(self):
    self.assertEqual(normalize_state(dict(V3_STATE, issues=[{"id": 1}]))["issues"], [])
def test_fit_keeps_200_closed_issues_with_seen_present(self):
    st = fit_state(state_with(closed=200, seen=SEEN_OVER_BUDGET))
    self.assertEqual(len(normalize_state(st)["issues"]), 200)
    self.assertLessEqual(len(state_marker(st)), MARKER_BUDGET)
def test_fit_drops_display_of_closed_before_open(self):
    st = fit_state(state_with(closed=1500, open_=50, long_titles=True))
    self.assertTrue(all(i["title"] for i in st["issues"] if i["status"] == "open"))
def test_fit_never_drops_an_open_issue(self):
    st = fit_state(state_with(open_=800, long_titles=True))
    self.assertEqual(sum(i["status"] == "open" for i in normalize_state(st)["issues"]), 800)
def test_fit_drops_the_oldest_closed_only_as_a_last_resort(self):
    st = fit_state(state_with(closed=3000))
    self.assertGreater(st["dropped_closed"], 0)
    self.assertLessEqual(len(state_marker(st)), MARKER_BUDGET)
def test_body_says_when_closed_records_were_dropped(self):
    body = render_body(REVIEW, [], META, dict(STATE, dropped_closed=3))
    self.assertIn("오래된 종료 이슈 기록 3건 삭제", body)
```

Scope tests with a real temp repo:

- `test_issue_follows_a_rename_and_shift`: one push does both, and the issue keeps its id (Review Focus 5).
- `test_v2_state_gets_no_duplicate_comment`: a v2 state whose finding is re-reported on the same line yields no new finding (Review Focus 1).
- `test_missing_previous_sha_does_not_fail`: `determine` with an unknown `prev["sha"]` returns with `weak_tracking` true (Review Focus 2).
- `test_closed_issue_stays_in_the_next_state`: a `waived` issue survives `next_state`.
- `test_new_finding_becomes_an_open_issue`: `next_state` writes it with `lo == hi == line`.

`tests/test_previous.py` and `tests/test_scope_moved.py` pinned anchor-based moves. Rewrite them: moved code is not followed, and the issue becomes an orphan.

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_validate tests.test_state_fit tests.test_scope_state`
Expected: failures on the new tests.

- [ ] **Step 3: Implement `normalize_state` and `fit_state`.** The `fit_state` order, until the marker fits: compact seen into hashes (as today), drop seen, seen_files and seen_hunks, move closed issues to the compact `closed` map, blank `title` and `excerpt` of open non-blocking issues, then remove the oldest compact records and set `dropped_closed`. Open issues and `recheck_pending` are never removed. `render_body` adds the line `오래된 종료 이슈 기록 n건 삭제` when `dropped_closed` is set.

- [ ] **Step 4: Implement `determine` and `next_state`.** Replace `relocate`: get `issues`, advance them with `diff_u0` and `parse_u0`, split them into open and closed, and build `previous`. A file not in `live` gives `pre="resolved"` with the existing notes, an orphan gives `pre="orphan"`, and the rest keep today's meaning of `ask` and `open`. `located` is true unless the fallback lost the issue. A closed issue whose file is gone gets `orphan: True`. `_path_map` reads `issues`. `next_state` keeps entries with status `open` or `unclear` as open issues, adds closed ones from `scope["closed"]` and this run's newly resolved ones, and puts `new_issue` records first (state order is newest first).

- [ ] **Step 5: Run the full suite**

Run: `python3 -m unittest discover -s tests`
Expected: OK

- [ ] **Step 6: Commit**: `feat: store the issue ledger in state v3`
