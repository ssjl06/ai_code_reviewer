# Plan Part 1 — Verdict Fixes

Read `00-overview.md` first. Spec sections 6.5 and 8. These fix bugs in the current code
and do not depend on the fingerprint work.

### Task 1: `partial` chunks, skipped files and unclear blockers block APPROVE

**Files:**
- Modify: `review/claude_runner.py` (`run_chunk`), `review/aggregate.py` (`merge`, `_collect`,
  `_previous`, `_summaries`), `review/render.py` (`_reason`), `review/scope.py` (`next_state`),
  `review/cli.py` (pass `skipped`), `tests/fakes/fake_claude.py` (scenario `partial`)
- Test: `tests/test_aggregate.py`, `tests/test_claude_runner.py`, `tests/test_render.py`,
  `tests/test_scope.py`, `tests/test_integration.py`

**Interfaces:**
- Produces:
  - `run_chunk` returns status `"partial"` when the extracted output (first attempt or the resume
    fallback) has a non-empty `incomplete_reason`; `error = "미완료 사유: " + reason[:200]`.
  - `merge(results, cfg, previous, changed, delta_files, no_targets, skipped=None)`; the Review
    gains `"skipped": list(skipped or [])`, `"unclear_blocking": bool`, `"complete": bool`.
  - `aggregate.OK_STATUSES = ("ok", "partial")` — chunk outputs with these statuses contribute
    findings, previous statuses and summaries.

Verdict rules (in this order):

```text
incomplete  = sorted indexes with status != "ok"          (partial counts as incomplete)
unclear_blocking = any previous p with p["blocking"] and final status "unclear"
complete    = not incomplete and not skipped and not failed_all
event = REQUEST_CHANGES if any blocking (unchanged rule)
      else COMMENT if no_targets or incomplete or failed_all or skipped or unclear_blocking
      else APPROVE
```

`render._reason` for COMMENT, first match wins: failed_all → `AI 리뷰 실패`; no_targets →
`리뷰 대상 변경 없음`; unclear_blocking → `이전 차단 지적 확인 필요`; incomplete →
`리뷰 미완료 조각 있음`; skipped → `생략된 파일 있음 (조각 수 한도)`.

`scope.next_state` keeps the previous sha (or "") when `not review["complete"]` (replaces the
`failed_all or incomplete` test). The fingerprint plan replaces this rule in Task 5.

- [ ] **Step 1: Add scenarios to `tests/fakes/fake_claude.py`**: `partial` = `OUT` with
  `incomplete_reason: "탐색 예산 부족"`; `silent` = `{"summary": "s", "findings": [],
  "previous_findings": [], "incomplete_reason": None}`.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_claude_runner.py
def test_incomplete_reason_is_partial(self):
    r = self.run_scn("partial")
    self.assertEqual(r["status"], "partial")
    self.assertEqual(r["error"], "미완료 사유: 탐색 예산 부족")
    self.assertEqual(len(r["output"]["findings"]), 1)          # findings kept

# tests/test_aggregate.py  (extend helper: run(..., skipped=None) passes skipped=skipped)
def test_partial_keeps_findings_but_blocks_approve(self):
    r = run([res(0, [F(sev="minor")], status="partial", reason="x")])
    self.assertEqual((len(r["findings"]), r["event"], r["complete"]), (1, "COMMENT", False))
def test_partial_blocking_finding_requests_changes(self):
    r = run([res(0, [F()], status="partial", reason="x")])
    self.assertEqual(r["event"], "REQUEST_CHANGES")
def test_skipped_files_block_approve(self):
    r = run([res(0, [])], skipped=["big.cu"])
    self.assertEqual((r["event"], r["skipped"], r["complete"]), ("COMMENT", ["big.cu"], False))
def test_previous_unreported_touched_is_unclear_and_blocks_approve(self):  # replaces ..._not_blocking
    r = run([res(0, [])], [P(blocking=True)], {}, {"a.cu"})
    self.assertEqual((r["previous"][0]["status"], r["event"]), ("unclear", "COMMENT"))
    self.assertTrue(r["unclear_blocking"])
def test_unclear_nonblocking_previous_still_approves(self):
    r = run([res(0, [])], [P(blocking=False)], {}, {"a.cu"})
    self.assertEqual(r["event"], "APPROVE")
def test_complete_when_all_ok(self):
    self.assertTrue(run([res(0, [])])["complete"])

# tests/test_render.py
def test_reason_unclear_blocking(self): ...   # COMMENT review with unclear_blocking -> "이전 차단 지적 확인 필요"
def test_reason_skipped(self): ...            # COMMENT review with skipped only -> "생략된 파일 있음 (조각 수 한도)"

# tests/test_scope.py
def test_next_state_keeps_prev_sha_when_skipped(self):
    review = {"findings": [], "previous": [], "complete": False, "skipped": ["x"]}
    self.assertEqual(next_state("h", review, {"sha": "p", "findings": []})["sha"], "p")

# tests/test_integration.py
def test_skipped_files_comment_not_approve(self):
    # two files, chunk_max_tokens small enough that each file is its own chunk, max_chunks=1
    self.gh.head = self.add_commit({"src/z.py": "z = 1\n" * 40})
    rc = self.run_main("silent", "--repo", self.R, "--pr", "1",
                       "--set", "max_chunks=1", "--set", "chunk_max_tokens=60")
    self.assertEqual(rc, 0)
    posted = self.gh.posted[-1]
    self.assertEqual(posted["event"], "COMMENT")
    self.assertIn("생략된 파일 있음 (조각 수 한도)", posted["body"])
    self.assertEqual(self.gh.dismissed, [])
```

- [ ] **Step 3: Run** `python3 -m unittest tests.test_aggregate tests.test_claude_runner tests.test_render tests.test_scope tests.test_integration`
  — Expected: the new tests FAIL.
- [ ] **Step 4: Implement** the rules above. In `cli._review` pass `skipped=skipped` to `merge`.
- [ ] **Step 5: Run** the full suite — Expected: all OK.
- [ ] **Step 6: Commit** `fix: never approve partial, skipped or unclear-blocking reviews`
