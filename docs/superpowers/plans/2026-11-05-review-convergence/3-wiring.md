# Review Convergence Implementation Plan (3 of 3: recheck, waivers, end to end)

Header, Global Constraints and Review Focus are in `1-ledger.md`, and they apply here.

---

### Task 6: Recheck and reviewer context

**Files:**
- Modify: `review/scope.py`, `review/ledger.py`, `review/prompt.py`, `review/cli.py`, `review/aggregate.py`, `agent/pr-reviewer.md`, `config/defaults.json`
- Test: `tests/test_scope_state.py`, `tests/test_ledger.py`, `tests/test_prompt.py`, `tests/test_schema_agent.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: `shown_lines`, `merge(..., closed=, shown=)`
- Produces:
  - Config `recheck_max_chars`: 8000
  - `ledger.assign_recheck(chunks: List[dict], previous: List[dict], blobs: Dict[str, List[str]], cfg: dict) -> Tuple[Dict[int, List[dict]], List[str]]`: chunk index -> items `{"id", "file", "lo", "hi", "start", "text"}`, and the ids left out
  - `ledger.pending_after(recheck: Dict[int, List[dict]], left_out: List[str], results: List[dict]) -> List[str]`
  - `review["recheck_pending"]`, written to the state by `next_state`
  - `ctx` for `build_prompt` gains `ledger` (open and closed issues), `orphans`, `recheck` (by chunk index)

Ruling on the spec's "recheck context counts toward the chunk size": chunks are built before recheck is assigned, and a chunk near its limit must still carry context, so recheck has its own budget of `recheck_max_chars` per chunk on top of the diff. Cost if wrong: a chunk is up to about 2,300 tokens larger than `chunk_max_tokens`.

- [ ] **Step 1: Write the failing tests**

```python
def test_null_check_above_the_issue_is_rechecked(self):
    """Flagged line unchanged, fix added above it."""
    s = determine_after_push(add_lines_at=1, issue_line=43)
    self.assertEqual(s["mode"], "incremental")
    self.assertTrue(any(p["id"] == "i1" and p["pre"] == "ask" for p in s["previous"]))
    self.assertNotIn(44, s["changed"].get("a.cc", set()))
def test_fix_in_another_file_still_rechecks(self):
    s = determine_after_push(change_file="b.cc", issue_file="a.cc", issue_line=43)
    self.assertIn("a.cc", {t["path"] for t in s["targets"]})
def test_same_sha_with_pending_recheck_is_not_unchanged(self):
    self.assertNotEqual(determine_same_sha(recheck_pending=["i1"])["mode"], "unchanged")
def test_same_sha_complete_with_nothing_pending_is_unchanged(self):
    self.assertEqual(determine_same_sha(recheck_pending=[])["mode"], "unchanged")
def test_recheck_context_has_20_lines_each_side(self):
    by, left = assign_recheck([chunk("a.cc")], [prev("i1", "a.cc", 50, 50)],
                              {"a.cc": ["x"] * 100}, {"recheck_max_chars": 8000})
    self.assertEqual((by[0][0]["start"], by[0][0]["text"].count("\n") + 1, left), (30, 41, []))
def test_recheck_over_budget_is_left_out(self):
    by, left = assign_recheck([chunk("a.cc")], [prev("i1", "a.cc", 50, 50)],
                              {"a.cc": ["x" * 500] * 100}, {"recheck_max_chars": 8000})
    self.assertEqual((by, left), ({}, ["i1"]))
def test_recheck_in_a_failed_chunk_is_pending(self):
    self.assertEqual(pending_after({0: [{"id": "i1"}]}, ["i2"], [{"index": 0, "status": "failed"}]),
                     ["i2", "i1"])
def test_pending_does_not_change_the_verdict(self):
    self.assertEqual(run_blocking_with_pending()["event"], "REQUEST_CHANGES")
def test_prompt_lists_open_and_closed_with_the_rules(self):
    p = build_prompt(chunk("a.cc"), ctx(ledger=[open_i, closed_i]))
    self.assertIn(closed_i["id"], p)
    self.assertIn("same_as", p)
def test_prompt_lists_orphans_of_other_files(self):
    p = build_prompt(chunk("b.cc"), ctx(orphans=[orphan_in("a.cc")]))
    self.assertIn("orphan-id", p)
def test_orphans_are_capped_at_4000_chars_newest_first(self):
    p = build_prompt(chunk("b.cc"), ctx(orphans=[orphan_n(i) for i in range(500)]))
    self.assertIn("orphan-0", p)
    self.assertNotIn("orphan-499", p)
```

`tests/test_schema_agent.py`: the agent file mentions `same_as`. `tests/test_config.py`: the new default.

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_scope_state tests.test_ledger tests.test_prompt`
Expected: failures on the new tests.

- [ ] **Step 3: Scope.** Rechecks are all open issues when `prev["sha"] != head`, and the ids in `recheck_pending` on the same sha. The early `unchanged` return also needs an empty `recheck_pending`. `_recheck` covers every recheck issue, not only lost ones, and sets `pre="ask"`; files already in `tpaths` need no new target. `changed` does not grow.

- [ ] **Step 4: Prompt and agent.** Add a section in Korean with the ledger of the chunk's files, each with id, region, severity, status, title and excerpt, and the rules "다시 지적하지 않는다" and "같은 문제면 `same_as`에 그 id를 쓴다". Add a section with orphans (id, severity, status, title), and a section with recheck context, as data lines, marked as not for new findings. Update "증분 리뷰 규칙" in `agent/pr-reviewer.md` to match.

- [ ] **Step 5: CLI.** After `build_chunks`, call `assign_recheck`; pass `shown_lines(chunks, recheck)` and `closed` to `merge`; set `review["recheck_pending"]` from `pending_after`. Log each duplicate, each link and the orphans left out. Log `이전 커밋을 찾을 수 없어 위치 추적이 약해짐` when `weak_tracking` is true.

- [ ] **Step 6: Run the full suite**

Run: `python3 -m unittest discover -s tests`
Expected: OK

- [ ] **Step 7: Commit**: `feat: recheck every open issue and show the ledger to the reviewer`

### Task 7: Dismissal is a waiver

**Files:**
- Modify: `review/github_api.py`, `review/ledger.py`, `review/scope.py`, `review/cli.py`, `review/render.py`
- Test: `tests/test_github_api.py`, `tests/test_ledger.py`, `tests/test_scope_state.py`, `tests/test_integration_state.py`

**Interfaces:**
- Produces:
  - `GitHub.dismissed_ids(bot_login: str) -> Set[str]`: open issue ids in the markers of bot reviews dismissed by someone else. It reads `/repos/<r>/issues/<n>/events` (paginated, `event == "review_dismissed"`, with `actor.login` and `dismissed_review.review_id`). It accepts v2 and v3 markers.
  - `ledger.apply_waivers(state: dict, ids: Set[str]) -> dict`: sets `waived_now` to the number of open issues changed; applying it twice gives 0
  - `determine` returns mode `settle` when `prev["sha"] == head`, `complete` is true and `waived_now > 0`
  - `sc.settle_state(head: str, review: dict, scope: dict) -> dict`: seen data copied from `scope["prev"]`

- [ ] **Step 1: Write the failing tests**

```python
def test_dismissal_by_a_person_waives_the_open_ids(self):
    self.assertEqual(gh_with(dismissed_by="alice").dismissed_ids("bot"), {"i1"})
def test_dismissal_by_the_bot_is_not_a_waiver(self):
    self.assertEqual(gh_with(dismissed_by="BOT").dismissed_ids("bot"), set())
def test_events_are_paginated(self):
    self.assertEqual(gh_with(dismissed_by="alice", events_pages=2).dismissed_ids("bot"), {"i1"})
def test_a_marker_without_state_is_skipped(self):
    self.assertEqual(gh_with(dismissed_by="alice", body="no marker").dismissed_ids("bot"), set())
def test_applying_a_waiver_twice_is_a_noop(self):
    st = apply_waivers(state_with_open("i1"), {"i1"})
    self.assertEqual(apply_waivers(st, {"i1"})["waived_now"], 0)
def test_a_closed_issue_is_not_changed_by_a_waiver(self):
    st = apply_waivers(state_with_resolved("i1"), {"i1"})
    self.assertEqual(st["issues"][0]["status"], "resolved")
def test_new_waiver_on_the_same_sha_settles_without_a_model(self):
    r = run_cli_same_sha(dismissed=["i1"])
    self.assertEqual(r.model_calls, 0)
    self.assertEqual(r.posted_event, "APPROVE")
    self.assertEqual(r.state["seen"], r.prev["seen"])
def test_settle_keeps_blocking_when_another_issue_is_open(self):
    self.assertEqual(run_cli_same_sha(dismissed=["i1"], also_open="i2").posted_event,
                     "REQUEST_CHANGES")
def test_dismissal_made_during_a_run_applies_on_the_next_run(self):
    r = run_cli_twice(dismiss_between_read_and_post="i1")
    self.assertEqual(r.second.posted_event, "APPROVE")
def test_waived_issue_stays_waived_after_a_push(self):
    self.assertEqual(run_cli_with_push(dismissed=["i1"]).issue("i1")["status"], "waived")
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_github_api tests.test_ledger tests.test_integration_state`
Expected: failures on the new tests.

- [ ] **Step 3: Implement.** In `_inputs`, read `dismissed_ids` after `latest_state` and apply it to the normalized state. In `_review`, a `settle` run skips `build_chunks` and `run_all`, calls `merge` with `no_targets=False` and no results, writes `settle_state`, and posts through the existing `post`. `post` already lifts earlier change requests on `APPROVE`. `render` shows `면제 n건` when `waived_now > 0`. A failed events call goes the way of any `GitHubError` in `main`.

- [ ] **Step 4: Run the full suite**

Run: `python3 -m unittest discover -s tests`
Expected: OK

- [ ] **Step 5: Commit**: `feat: treat a person's dismissal as a waiver`

### Task 8: End to end and docs

**Files:**
- Create: `tests/test_convergence.py`
- Modify: `README.md`, `docs/runbook.md`, `docs/superpowers/specs/2026-10-18-ai-code-review-design.md`

- [ ] **Step 1: Write the scenario tests.** Use a real temp repo, a fake model as in `tests/test_integration*.py`, and at least three pushes per scenario.

```python
def test_reworded_reraise_converges(self):
    """The model raises one issue in new words on a neighboring line on every push."""
    r = scenario(pushes=3, reraise=True)
    self.assertEqual(r.inline_comments_per_run, [1, 0, 0])
def test_large_block_inserted_in_a_closed_region_keeps_new_findings(self):
    r = scenario_insert_1000_lines_in_closed_region()
    self.assertEqual(r.last.new_findings, 1)
def test_waiver_does_not_cross_to_another_variable(self):
    """A waived 5-line block is deleted; the same block on another variable lands in another file."""
    self.assertEqual(scenario_block_on_other_variable().last.new_findings, 1)
def test_moved_function_with_same_as_stays_waived(self):
    r = scenario_move(lines=40, same_as=True)
    self.assertEqual((r.last.new_findings, r.issue("i1")["status"]), (0, "waived"))
def test_moved_function_without_same_as_is_raised_once_more(self):
    self.assertEqual(scenario_move(lines=40, same_as=False).last.new_findings, 1)
def test_short_move_to_another_chunk_links_through_the_orphan_list(self):
    r = scenario_short_move_across_chunks()
    self.assertIn("i1", r.last.prompts[r.destination_chunk])
    self.assertEqual(r.last.new_findings, 0)
def test_skipped_chunk_cannot_close_and_the_retry_reports(self):
    r = scenario_skipped_chunk_then_retry()
    self.assertEqual(r.first.issue("i1")["status"], "open")
    self.assertEqual(r.second.issue("i1")["status"], "resolved")
```

- [ ] **Step 2: Run them.** They should pass; a failure here is a defect in an earlier task, so fix it there.

Run: `python3 -m unittest tests.test_convergence`
Expected: OK

- [ ] **Step 3: Update the docs.** README: state v3 and that dismissal waives the review's open issues. Runbook: what `기존 이슈와 중복되어 생략 n건`, `dropped_closed` and the weak tracking log line mean. Base design spec: sections 6 and 8 point to the convergence spec.

- [ ] **Step 4: Run the full suite**

Run: `python3 -m unittest discover -s tests`
Expected: OK

- [ ] **Step 5: Commit**: `test: cover convergence end to end and update the docs`
