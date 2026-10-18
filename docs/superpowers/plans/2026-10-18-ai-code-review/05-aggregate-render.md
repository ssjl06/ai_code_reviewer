# Plan Part 5 — Aggregate/Verdict and Rendering

Read `00-overview.md` first. Spec sections 6, 8, 9.2.

### Task 8: Merge findings and compute the verdict

**Files:**
- Create: `review/aggregate.py`
- Test: `tests/test_aggregate.py`

**Interfaces:**
- Consumes: `ChunkResult` (Task 7), `Config`, `State` findings (Task 9 parses them).
- Produces:
  - `finding_id(f: dict) -> str` — `sha1(f"{file}:{line}:{title}")` hex, first 8 chars.
  - `normalize_path(p: str) -> str` — strips leading `./`, `a/`, `b/`, `/`.
  - `merge(results: list[ChunkResult], cfg: dict, previous: list[dict], changed: dict[str, set[int]] | None,
    delta_files: set[str] | None, no_targets: bool) -> Review`
    - `changed`: added lines of the incremental range (None for a full review).
    - `delta_files`: files touched by the incremental range (None for a full review).
  - `split_inline(findings: list[Finding], right_lines: dict[str, set[int]]) -> tuple[list, list]`
    — (inline, body_only); inline iff `line` in `right_lines[file]`.

Merge rules, in order:

1. Collect findings from `status == "ok"` results; normalize `file`; drop `confidence < min_confidence`
   (count in `dropped`).
2. If `changed` is not None: drop findings whose `line` is not in `changed[file]` (count in `dropped`).
3. Dedupe: same file, same category, `abs(line diff) <= 2` → keep the higher (severity rank, confidence).
4. Annotate: `id`, `blocking = severity in block_severities and confidence >= block_confidence`,
   `needs_check = not blocking and severity in block_severities and confidence < block_confidence`.
5. Previous findings: status = worst of reported statuses for that id (`open` > `unclear` > `resolved`);
   unreported and file not in `delta_files` → `open`; unreported and file in `delta_files` → `unclear`.
   A previous finding re-reported as new (same id) is removed from the new list.
6. Event: `REQUEST_CHANGES` if any new blocking or any previous `blocking` with status `open`;
   else `COMMENT` if `no_targets`, any result not `ok` (indices in `incomplete`), or all failed;
   else `APPROVE`. `failed_all = results and all(status == "failed")`.
7. Sort findings by (severity rank, -confidence, file, line).

- [ ] **Step 1: Write `tests/test_aggregate.py`** with helper `res(i, findings, status="ok", prev=())`.

```python
def test_blocking_boundary_79_80(self):
    r = merge([res(0, [F(sev="major", conf=79)])], CFG, [], None, None, False)
    self.assertEqual(r["event"], "APPROVE"); self.assertTrue(r["findings"][0]["needs_check"])
    r = merge([res(0, [F(sev="major", conf=80)])], CFG, [], None, None, False)
    self.assertEqual(r["event"], "REQUEST_CHANGES"); self.assertTrue(r["findings"][0]["blocking"])
def test_minor_high_confidence_not_blocking(self):
    self.assertEqual(merge([res(0, [F(sev="minor", conf=99)])], CFG, [], None, None, False)["event"], "APPROVE")
def test_low_confidence_dropped(self):
    r = merge([res(0, [F(conf=49)])], CFG, [], None, None, False)
    self.assertEqual((r["findings"], r["dropped"]), ([], 1))
def test_incomplete_blocks_approve(self):
    r = merge([res(0, []), res(1, [], status="incomplete")], CFG, [], None, None, False)
    self.assertEqual((r["event"], r["incomplete"]), ("COMMENT", [1]))
def test_blocking_wins_over_incomplete(self): ...   # -> REQUEST_CHANGES
def test_all_failed(self):
    r = merge([res(0, [], status="failed")], CFG, [], None, None, False)
    self.assertEqual((r["event"], r["failed_all"]), ("COMMENT", True))
def test_no_chunks_is_comment(self):
    r = merge([], CFG, [], None, None, True)
    self.assertEqual((r["event"], r["no_targets"]), ("COMMENT", True))
def test_incremental_filter_drops_unchanged_lines(self):
    r = merge([res(0, [F(file="a.cu", line=10), F(file="a.cu", line=50)])], CFG, [],
              {"a.cu": {50}}, {"a.cu"}, False)
    self.assertEqual([f["line"] for f in r["findings"]], [50])
def test_previous_open_blocks(self):
    prev = [P(id="f1", file="a.cu", blocking=True)]
    r = merge([res(0, [], prev=[("f1", "open")])], CFG, prev, {}, {"a.cu"}, False)
    self.assertEqual(r["event"], "REQUEST_CHANGES")
def test_previous_untouched_file_stays_open(self):
    prev = [P(id="f1", file="b.cu", blocking=True)]
    r = merge([res(0, [])], CFG, prev, {}, {"a.cu"}, False)
    self.assertEqual(r["previous"][0]["status"], "open"); self.assertEqual(r["event"], "REQUEST_CHANGES")
def test_previous_unreported_touched_is_unclear_not_blocking(self): ...   # -> unclear, APPROVE
def test_previous_resolved_approves(self): ...                           # -> APPROVE
def test_dedupe_keeps_stronger(self):
    r = merge([res(0, [F(line=10, sev="minor", conf=90)]), res(1, [F(line=11, sev="major", conf=85)])],
              CFG, [], None, None, False)
    self.assertEqual([(f["line"], f["severity"]) for f in r["findings"]], [(11, "major")])
def test_normalize_paths_and_lines(self):
    r = merge([res(0, [F(file="./src/a.cu", line=0), F(file="b/src/a.cu", line=12)])], CFG, [], None, None, False)
    self.assertEqual({f["file"] for f in r["findings"]}, {"src/a.cu"})
    inline, body = split_inline(r["findings"], {"src/a.cu": {12}})
    self.assertEqual(([f["line"] for f in inline], [f["line"] for f in body]), ([12], [0]))
```

- [ ] **Step 2: Run** `python3 -m unittest tests.test_aggregate -v` — Expected: FAIL.
- [ ] **Step 3: Implement `review/aggregate.py`** per the rules (severity rank critical 0 … info 3).
- [ ] **Step 4: Run** `python3 -m unittest discover -s tests -v` — Expected: all OK.
- [ ] **Step 5: Commit** `feat: merge findings and compute verdict`

---

### Task 9: Review body, inline comments, state marker, step summary

**Files:**
- Create: `review/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `Review`, `Finding`, `Stats`.
- Produces:
  - `MARKER = "ai-review-state"`; `MAX_BODY = 60000` (GitHub limit is 65536).
  - `state_marker(state: State) -> str` — `<!-- ai-review-state {compact json} -->`.
  - `parse_state(body: str | None) -> State | None` — None on missing or corrupt marker.
  - `render_inline(f: Finding) -> str` — `**[{severity} · 확신 {confidence} · {category}]** {title}\n\n{detail}`
    plus `\n\n**제안**\n{suggestion}` when present; append ` ⚠️ 확인 필요` to the header when `needs_check`.
  - `render_body(review: Review, body_only: list[Finding], meta: dict, state: State) -> str`
    where `meta = {"model", "chunks": int, "duration_s": float, "range": str, "mode": "full|incremental",
    "reviewed": list[str], "skipped": list[str]}`.
  - `render_step_summary(results: list[ChunkResult]) -> str` — markdown table
    `| chunk | status | turns | wall | LLM | tools | tool calls | denied | tokens in/out |`.

Body layout (Korean, sections omitted when empty):

```text
## 🤖 AI Code Review — {✅ 승인 | ❌ 변경 요청 | 💬 코멘트}
**판정 근거:** 머지 차단 N건 (critical a, major b) | 차단 사유 없음 | 리뷰 미완료 조각 있음 | 리뷰 대상 변경 없음 | AI 리뷰 실패
| 심각도 | 건수 |  (critical/major/minor/info)
### 이전 지적 처리 현황        (incremental: ✅ 해결 / ⏳ 미해결 / ❔ 불명확, one line each)
### 확인 필요 (confidence < 80)
### inline으로 달 수 없는 지적  (render_inline of each body_only finding, prefixed with `file:line`)
### 조각별 요약                 (review["summaries"])
### 리뷰 범위                   리뷰 N files · 생략 M files (list) · 미완료 조각 (indices)
<sub>{model} · {chunks} chunks · {mm}m{ss}s · {range}</sub>
<!-- ai-review-state {...} -->
```

If the body exceeds `MAX_BODY`, truncate sections before the footer and add
`(본문 길이 제한으로 일부 생략)`; the footer and marker are always kept.

- [ ] **Step 1: Write `tests/test_render.py`**

```python
def test_state_round_trip(self):
    st = {"sha": "abc", "findings": [{"id": "f1", "file": "a.cu", "line": 3, "title": "t",
                                      "severity": "major", "blocking": True}]}
    self.assertEqual(parse_state("text\n" + state_marker(st)), st)
def test_parse_state_corrupt(self):
    for b in [None, "", "no marker", "<!-- ai-review-state {bad json -->"]:
        self.assertIsNone(parse_state(b))
def test_inline_format(self):
    s = render_inline(FIND(sev="major", conf=85, cat="concurrency", title="T", suggestion="S"))
    self.assertTrue(s.startswith("**[major · 확신 85 · concurrency]** T")); self.assertIn("**제안**\nS", s)
def test_body_headers_per_event(self):
    for ev, head in [("APPROVE", "✅ 승인"), ("REQUEST_CHANGES", "❌ 변경 요청"), ("COMMENT", "💬 코멘트")]:
        self.assertIn(head, render_body(REVIEW(event=ev), [], META, ST))
def test_body_previous_section_incremental_only(self): ...
def test_body_no_targets_reason(self): ...      # "리뷰 대상 변경 없음"
def test_body_truncated_keeps_marker(self):
    b = render_body(REVIEW(summaries=["x" * 70000]), [], META, ST)
    self.assertLessEqual(len(b), 65536); self.assertIsNotNone(parse_state(b))
    self.assertIn("본문 길이 제한", b)
def test_step_summary_table(self):
    t = render_step_summary([RESULT(index=0, status="ok")])
    self.assertIn("| chunk | status |", t); self.assertIn("| 1 | ok |", t)
```

- [ ] **Step 2: Run** `python3 -m unittest tests.test_render -v` — Expected: FAIL.
- [ ] **Step 3: Implement `review/render.py`.** Marker JSON via `json.dumps(..., ensure_ascii=False,
  separators=(",", ":"))` with `--` replaced by `--` so the HTML comment cannot be closed early.
- [ ] **Step 4: Run** `python3 -m unittest discover -s tests -v` — Expected: all OK.
- [ ] **Step 5: Commit** `feat: render review body, inline comments and state marker`
