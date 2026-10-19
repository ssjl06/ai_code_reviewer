# Plan Part 4 — Scope v2 and State v2

Read `00-overview.md` first. Spec sections 6.2, 6.3, 6.5.

### Task 5: content-based `scope.determine`, `review/seen.py`, state v2, cli wiring

**Files:**
- Create: `review/seen.py`
- Modify: `review/scope.py` (`determine`, `next_state`; drop the `is_ancestor` import),
  `review/validate.py` (`normalize_state`), `review/aggregate.py` (remove `delta_files` and the
  no-`pre` fallback from Task 4), `review/cli.py` (`_review` wiring)
- Test: `tests/test_scope.py` (rewrite the ancestry tests), `tests/test_seen.py`,
  `tests/test_validate.py`, `tests/test_aggregate.py`, `tests/test_integration.py` (update)

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces:
  - `scope.determine(repo: str, base: str, head: str, prev: Optional[StateV2], cfg: dict) -> Scope`:
    1. `pr_files = parse_unified_diff(diff_text(repo, base, head, merge_base=True))`;
       `range = "%s...%s" % (base[:7], head[:7])`; `renames = {old_path: path}` for renamed files.
    2. `prev` and `prev["sha"] == head` and `prev["complete"]` → `mode="unchanged"`, `targets=[]`.
    3. `rev = [f for f in pr_files if reviewable(f, cfg)]`; `reviewable = len(rev)`.
       `blobs = read_blobs(repo, head, paths of rev ∪ (renamed) files of prev findings that are in pr_files)`.
    4. `seen, whole = decode_seen(prev)`. Per file: `s = seen.get(path) or seen.get(old_path)`,
       `w = whole.get(path) or whole.get(old_path)`; `sel = select_file(f, blobs.get(path, []),
       None if prev is None else s, w)`. `head_prints[path] = sel["head"]`;
       `prev_seen[path] = sel["head"]` if `w` matched (`w == whole_hash(sel["head"])`) else `s or Counter()`.
       Non-empty `sel["file"]` → target; record `added[path]`, `deleted[path]`.
    5. `changed = None` if `prev is None` else `{p: added[p] | deleted[p]}` for target paths.
    6. `previous = relocate(prev findings, {f["path"] for f in pr_files}, blobs, target paths, renames)`.
    7. mode: `"full"` if `prev is None`; else `"unchanged"` if no targets and no entry has
       `pre == "resolved"`; else `"incremental"`.
  - `seen.update_seen(scope: Scope, chunks: List[Chunk], results: List[ChunkResult]) -> Seen`:
    `ok[path]` = union of `diff_prints(chunk["diff"], blobs)` over chunks whose result status is
    `"ok"`; `bad[path]` = prints of all other chunks ∪ (prints of `scope["targets"]` − `ok`).
    For each path in `head_prints`, each print `p`: count = `head[p]` if `p in ok and p not in bad`
    else `min(prev_seen[p], head[p])`; zero counts dropped; empty files dropped.
  - `scope.next_state(head: str, review: Review, scope: Scope, chunks, results) -> StateV2` —
    `sha=head`, `complete=review["complete"]`, `seen/seen_files = encode_seen(update_seen(...))`,
    findings = new findings + previous entries with status `open`/`unclear`, each reduced to
    `("id","file","line","anchor","title","severity","blocking")` (`anchor` default `""`).
    The Task 1 "keep previous sha" rule is removed (spec 6.5 supersedes it).
  - `validate.normalize_state(state) -> Optional[StateV2]` — `None` unless a dict with `v == 2`;
    `sha` str (default ""), `complete` bool (default False), `seen`/`seen_files` keep only
    str→str items, findings as before plus `anchor` str (default "").
  - `merge(results, cfg, previous, changed, no_targets, skipped=None, anchor=None)`.
  - `cli._review`: `scope = determine(repo, base, head, prev, cfg)`; unchanged → log
    `새로 리뷰할 변경 없음 — 리뷰를 건너뜀`, return 0; `previous = scope["previous"]`;
    `merge(results, cfg, previous, scope["changed"], scope["reviewable"] == 0, skipped=skipped,
    anchor=anchor_of(scope["blobs"]))`; `state = next_state(head, review, scope, chunks, results)`.
    Keep `cli.py` ≤ ~165 lines (move helpers into `scope.py` if needed).

- [ ] **Step 1: Write `tests/test_scope.py`** (temp repos; helper `state_for(scope)` = StateV2
  from `next_state` with every chunk `ok`; helper `branch(files)` commits on a feature branch)

```python
def test_first_review_full(self):           # prev None -> mode full, changed None, all reviewable files targeted
def test_same_sha_complete_unchanged(self):
def test_same_sha_incomplete_reviews_again(self):   # complete False, empty seen -> targets non-empty
def test_squash_unchanged(self):            # 2 commits reviewed, then `reset --soft base` + one commit -> unchanged
def test_rebase_onto_advanced_main_unchanged(self):  # main gains other.py; rebase; determine(new main, new head)
    self.assertEqual(s["mode"], "unchanged"); self.assertNotIn("other.py", [f["path"] for f in s["pr_files"]])
def test_merge_main_unchanged(self):
def test_rebase_with_edit_only_edit(self):  # one edited line -> one target file, changed == {path: {n-1, n, n+1} ∩ added}
def test_rename_without_edit_unchanged(self):
def test_neighbor_change_reviews_added_line(self):
def test_deletion_only_followup(self):      # delete a reviewed line -> deleted == {anchor}, mode incremental
def test_duplicate_paste_reviews_both_copies(self):
def test_dropped_file_resolves_previous(self):  # previous finding file reverted out of PR -> mode incremental, pre resolved
def test_next_state_v2_shape(self):
    st = next_state("h", review, scope, chunks, results)
    self.assertEqual(set(st), {"v", "sha", "complete", "seen", "seen_files", "findings"})
    self.assertEqual(st["v"], 2)
```

- [ ] **Step 2: Write `tests/test_seen.py`** (synthetic `Scope` dicts, no git)

```python
def test_ok_chunk_marks_seen(self): ...
def test_failed_chunk_lines_stay_new(self): ...      # prints only in a failed chunk keep prev counts
def test_skipped_target_stays_new(self): ...          # target file not in any chunk
def test_truncated_tail_stays_new(self): ...          # chunk diff cut with "[truncated by ai-review]"
def test_prev_counts_kept_for_untouched_files(self): ...
def test_whole_hash_file_counts_as_head(self): ...
```

- [ ] **Step 3: Update existing tests**: `tests/test_validate.py` (v1 state → `None`; v2 round trip;
  bad `seen` items dropped); `tests/test_aggregate.py` (drop `delta` argument from `run`; delete
  the `delta_files` tests superseded by the Task 4 `pre` tests); `tests/test_integration.py`:
  third-run log → `새로 리뷰할 변경 없음`; state equality → check `sha`, `complete`, `findings`;
  `test_dry_run_with_state_file_is_incremental` writes a v2 state produced by a first dry run's
  `review.json` body (`parse_state`); `test_llm_down_exit_1` → `complete` False and `seen == {}`;
  `test_failed_followup_keeps_change_request` → `complete` False.
- [ ] **Step 4: Run** `python3 -m unittest tests.test_scope tests.test_seen tests.test_validate tests.test_aggregate tests.test_integration` — Expected: new tests FAIL.
- [ ] **Step 5: Implement.**
- [ ] **Step 6: Run** the full suite — Expected: all OK.
- [ ] **Step 7: Commit** `feat: content-based review scope with state v2`
