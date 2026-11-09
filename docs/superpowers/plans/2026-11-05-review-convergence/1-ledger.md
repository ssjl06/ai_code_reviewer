# Review Convergence Implementation Plan (1 of 3: positions and matching)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repeated reviews of one PR converge: an issue is raised once, and once closed it never comes back.

**Architecture:** A ledger of issues replaces the `findings` list in the state marker (v3). Each issue tracks a file region that git diffs carry forward from push to push, so identity no longer depends on wording or line content. New findings are matched against the ledger in code, and closure requires that the model was shown the region.

**Tech Stack:** Python 3 stdlib, `unittest`, git CLI, GitHub REST API.

**Spec:** `docs/superpowers/specs/2026-11-02-review-convergence-design.md` (rev 5)

Tasks 1-3 are here, tasks 4-5 in `2-state.md`, tasks 6-8 in `3-wiring.md`. Run them in order.

## Global Constraints

- Stdlib only. Code, comments and docs in English; text shown to the LLM or on the PR in Korean.
- Full suite: `python3 -m unittest discover -s tests` (about 30 seconds). It must pass after every task.
- Match distance: 3 lines. `same_as` distance: 15 lines, unlimited for an orphan.
- Region radius: at most 5 lines on each side of the primary line.
- Recheck context: 20 lines on each side of the region. Description excerpt: 200 characters.
- Orphans in a prompt: at most 4000 characters per chunk, newest first.
- `resolved` and `waived` are closed, and a closed issue never reopens, whatever the new severity.
- Severity, confidence and blocking are frozen at the first judgment. Issue ids are never recomputed.
- The blocking rule (critical/major, confidence >= 80), COMMENT on incomplete runs, and "incremental reviews flag only newly changed lines" do not change.
- The LLM's tools stay read-only; this work adds no tool.

## Review Focus

1. A PR that already has a v2 marker: the first v3 run keeps every open finding, posts no duplicate comment, and a waiver on an old review still applies (test in Task 5).
2. A force-push whose previous sha was never fetched: the run does not fail; it falls back to anchors and logs the weaker guarantee (Task 2).
3. `same_as` that names an unknown id, a closed non-orphan issue in another file, or a non-string: the finding is kept as new, unless position matches (Task 3).
4. A run where every chunk fails: nothing closes, no issue is lost, and the state keeps the open issues (Task 4).
5. A file renamed and shifted in one push: the issue follows it, with no duplicate (Task 2).

---

### Task 1: Region mapping

**Files:**
- Create: `review/regions.py`, `tests/test_regions.py`
- Modify: `review/gitutil.py`

**Interfaces:**
- Produces:
  - `Hunk = Tuple[int, int, int, int]`, meaning `(old_start, old_len, new_start, new_len)` from `@@ -a,b +c,d @@`, with a missing length meaning 1
  - `parse_u0(diff: str) -> Dict[str, dict]`: old path -> `{"path": new_path, "deleted": bool, "hunks": List[Hunk]}`; built on `parse_unified_diff` and `HUNK_RE`
  - `map_line(hunks: List[Hunk], line: int) -> int`
  - `map_region(hunks: List[Hunk], line: int, lo: int, hi: int) -> Optional[Tuple[int, int, int]]`: `(line, lo, hi)`, or `None` for an orphan
  - `gitutil.diff_u0(repo: str, old: str, new: str) -> Optional[str]`: `git diff -U0 -M --no-color old new`; `None` when `old` is not a local commit, using the check `renames_between` already does

- [ ] **Step 1: Write the failing tests** in `tests/test_regions.py`

```python
def test_line_below_an_insertion_shifts(self):
    self.assertEqual(map_line([(10, 0, 11, 3)], 20), 23)
def test_line_above_a_hunk_stays(self):
    self.assertEqual(map_line([(10, 0, 11, 3)], 10), 10)
def test_rewritten_line_maps_proportionally(self):
    self.assertEqual(map_line([(10, 2, 10, 4)], 11), 12)
def test_deleted_line_maps_to_the_deletion_point(self):
    self.assertEqual(map_line([(10, 5, 9, 0)], 12), 9)
def test_whole_region_deleted_is_orphan(self):
    self.assertIsNone(map_region([(10, 5, 9, 0)], 11, 11, 12))
def test_partly_deleted_region_stays(self):
    self.assertEqual(map_region([(10, 5, 9, 0)], 12, 12, 16), (9, 9, 11))
def test_large_rewrite_is_capped_to_the_radius(self):
    self.assertEqual(map_region([(10, 100, 10, 100)], 50, 10, 109), (50, 45, 55))
def test_insertion_inside_a_region_does_not_widen_it(self):
    self.assertEqual(map_region([(100, 0, 101, 1000)], 100, 95, 105), (100, 95, 105))
def test_parse_follows_a_rename(self):
    out = parse_u0(RENAME_DIFF)
    self.assertEqual(out["old.cc"]["path"], "new.cc")
    self.assertEqual(out["old.cc"]["hunks"], [(1, 0, 2, 2)])
```

Also add a test with a real temp repo (see `Repo` in `tests/test_scope_regress.py`) that `diff_u0` returns text for two commits and `None` for a sha that does not exist.

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_regions`
Expected: ImportError for `review.regions`.

- [ ] **Step 3: Implement.** A hunk with `old_len == 0` inserts after `old_start`; `map_line` adds `new_len - old_len` for every hunk fully above the line. Inside a hunk with new lines, use `new_start + min(new_len - 1, (line - old_start) * new_len // old_len)`. Inside a hunk with `new_len == 0`, return `max(1, new_start)`. In `map_region`, return `None` when every line of `[lo, hi]` falls in hunks with `new_len == 0`. Otherwise map the three values and cut `lo` and `hi` to 5 lines around the mapped `line`.

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m unittest tests.test_regions tests.test_gitutil`
Expected: OK

- [ ] **Step 5: Commit**: `feat: map issue regions across pushes`

### Task 2: Ledger records and advancing them

**Files:**
- Create: `review/ledger.py`, `tests/test_ledger.py`

**Interfaces:**
- Consumes: `regions.map_region`, `regions.parse_u0`, `previous._closest`
- Produces:
  - Issue record: `{"id", "file", "line", "lo", "hi", "status", "severity", "blocking", "anchor", "title", "excerpt", "orphan"}`. `status` is `open`, `resolved` or `waived`; `orphan` is a bool; an orphan keeps `line`, `lo`, `hi` for display only.
  - `CLOSED = ("resolved", "waived")`
  - `new_issue(f: dict) -> dict`: from a merged finding; `lo == hi == line`, `excerpt == f["detail"][:200]`, status `open`
  - `from_v2(entry: dict) -> dict`: a v2 state finding as an open issue
  - `advance(issues: List[dict], u0: Optional[Dict[str, dict]], blobs: Dict[str, List[str]]) -> Tuple[List[dict], bool]`: new list, and whether the fallback was used

- [ ] **Step 1: Write the failing tests** in `tests/test_ledger.py`

```python
def test_issue_follows_a_shift_and_rename(self):
    u0 = {"a.cc": {"path": "b.cc", "deleted": False, "hunks": [(1, 0, 2, 2)]}}
    out, fb = advance([issue("a.cc", 10)], u0, {})
    self.assertEqual((out[0]["file"], out[0]["line"], out[0]["lo"], out[0]["hi"]),
                     ("b.cc", 12, 12, 12))
    self.assertFalse(fb)
def test_deleted_code_makes_an_orphan(self):
    u0 = {"a.cc": {"path": "a.cc", "deleted": False, "hunks": [(10, 1, 9, 0)]}}
    self.assertTrue(advance([issue("a.cc", 10)], u0, {})[0][0]["orphan"])
def test_deleted_file_makes_an_orphan(self):
    u0 = {"a.cc": {"path": "a.cc", "deleted": True, "hunks": [(1, 20, 0, 0)]}}
    self.assertTrue(advance([issue("a.cc", 10)], u0, {})[0][0]["orphan"])
def test_an_orphan_stays_an_orphan(self):
    out, _ = advance([dict(issue("a.cc", 10), orphan=True)], {}, {})
    self.assertTrue(out[0]["orphan"])
def test_no_previous_sha_uses_the_anchor(self):
    blobs = {"a.cc": ["x"] * 30}
    i = dict(issue("a.cc", 10), anchor=fp_line(blobs["a.cc"], 10).hex())
    out, fb = advance([i], None, blobs)
    self.assertTrue(fb)
    self.assertEqual(out[0]["line"], 10)
def test_no_previous_sha_and_no_anchor_keeps_the_region(self):
    out, fb = advance([dict(issue("a.cc", 10), anchor="")], None, {"a.cc": []})
    self.assertEqual((out[0]["lo"], out[0]["hi"]), (10, 10))
    self.assertTrue(fb)
def test_ids_never_change(self):
    self.assertEqual(from_v2(V2)["id"], V2["id"])
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_ledger`
Expected: ImportError for `review.ledger`.

- [ ] **Step 3: Implement.** `advance` copies each issue and never changes `id`, `status`, `severity` or `blocking`. A file without an entry in `u0` keeps its region. When `u0` is `None`, relocate with `previous._closest` and shift the region by the same distance.

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m unittest tests.test_ledger`
Expected: OK

- [ ] **Step 5: Commit**: `feat: add the issue ledger and advance it across pushes`

### Task 3: Matching new findings

**Files:**
- Modify: `review/ledger.py`, `review/validate.py`, `config/review-schema.json`, `review/aggregate.py`, `review/render.py`
- Test: `tests/test_ledger.py`, `tests/test_validate.py`, `tests/test_korean.py`, `tests/test_aggregate_prev.py`

**Interfaces:**
- Produces:
  - `classify(findings: List[dict], issues: List[dict]) -> Tuple[List[dict], List[dict], List[dict]]`: `(new, duplicates, links)`. Each duplicate is the finding with `dup_of` set. Each link is `{"id", "file", "line"}`.
  - `unique_id(f: dict, taken: Set[str]) -> str`: `finding_id(f)`, or a rehash with a counter suffix if taken
  - `merge(..., closed: Optional[List[dict]] = None)`; the result gains `duplicates` and `links`
  - A finding may carry `same_as` (string)

- [ ] **Step 1: Write the failing tests**

```python
def test_reworded_finding_near_an_open_issue_is_merged(self):
    new, dups, _ = classify([finding("a.cc", 12, title="다른 표현")], [issue("a.cc", 10)])
    self.assertEqual((new, len(dups)), ([], 1))
def test_finding_four_lines_away_is_new(self):
    self.assertEqual(len(classify([finding("a.cc", 14)], [issue("a.cc", 10)])[0]), 1)
def test_closed_match_is_dropped_even_as_critical(self):
    new, dups, _ = classify([finding("a.cc", 10, severity="critical")],
                            [dict(issue("a.cc", 10), status="resolved")])
    self.assertEqual((new, len(dups)), ([], 1))
def test_same_as_within_15_lines_links(self):
    self.assertEqual(classify([finding("a.cc", 25, same_as="i1")],
                              [issue("a.cc", 10, id="i1")])[0], [])
def test_same_as_beyond_15_lines_is_ignored(self):
    self.assertEqual(len(classify([finding("a.cc", 26, same_as="i1")],
                                  [issue("a.cc", 10, id="i1")])[0]), 1)
def test_same_as_in_another_file_is_ignored(self):
    self.assertEqual(len(classify([finding("b.cc", 10, same_as="i1")],
                                  [issue("a.cc", 10, id="i1")])[0]), 1)
def test_same_as_links_an_orphan_anywhere(self):
    new, _, links = classify([finding("b.cc", 500, same_as="i1")],
                             [dict(issue("a.cc", 10, id="i1"), orphan=True)])
    self.assertEqual((new, links), ([], [{"id": "i1", "file": "b.cc", "line": 500}]))
def test_position_never_matches_an_orphan(self):
    self.assertEqual(len(classify([finding("a.cc", 10)],
                                  [dict(issue("a.cc", 10), orphan=True)])[0]), 1)
def test_unknown_same_as_is_kept_as_new(self):
    self.assertEqual(len(classify([finding("a.cc", 10, same_as="nope")], [])[0]), 1)
```

In `tests/test_validate.py`: a string `same_as` is kept; a non-string is dropped. In `tests/test_korean.py`: a Korean re-ask keeps `same_as`. In `tests/test_aggregate_prev.py`: a merged re-raise as a blocking critical does not create a new finding, the issue keeps its severity, and `review["duplicates"]` has one entry.

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_ledger tests.test_validate tests.test_korean tests.test_aggregate_prev`
Expected: failures on the new tests.

- [ ] **Step 3: Implement.** In `classify`, try `same_as` first, then position, preferring an open issue over a closed one. In `merge`, replace the `prev_ids` and `prev_keys` filter with `classify(findings, previous + (closed or []))`, and assign ids with `unique_id`. Rewrite the existing tests that pinned "a re-raise that is now blocking is kept". In `_sections`, add `기존 이슈와 중복되어 생략 n건` when `duplicates` is not empty. Add `same_as` to the finding properties of the schema. Until Task 5, `previous` entries have no `lo` or `hi`, so `classify` reads them with `line` as the default.

- [ ] **Step 4: Run the full suite**

Run: `python3 -m unittest discover -s tests`
Expected: OK

- [ ] **Step 5: Commit**: `feat: match new findings against the ledger`
