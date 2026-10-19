# Plan Part 5 — Prompt, Agent and Force-Push Integration

Read `00-overview.md` first. Spec sections 6.3 (item 6), 6.4, 7.1, 8, 9.2, 11.

### Task 6: prompt focus lines, agent rules, force-push integration tests, runbook

**Files:**
- Modify: `review/prompt.py`, `agent/pr-reviewer.md`, `review/cli.py` (ctx fields),
  `review/render.py` (previous section), `docs/runbook.md`
- Test: `tests/test_prompt.py`, `tests/test_schema_agent.py`, `tests/test_render.py`,
  Create `tests/test_integration_force.py` (imports `IntegrationBase` from `tests.test_integration`)

**Interfaces:**
- Consumes: `Scope["added"]`, `Scope["deleted"]`, `Scope["previous"]` (Task 5).
- Produces:
  - `build_prompt(chunk, ctx)` reads `ctx["added"]`, `ctx["deleted"]`, `ctx["previous"]`:
    - mode line: full → unchanged text; incremental → `증분 리뷰: 이전 리뷰 이후 새로 바뀐 줄만 리뷰한다.`
    - incremental only: `검토 대상 줄:` then `- <path>: <ranges>` per chunk file with added lines,
      and `삭제 지점:` then `- <path>: <ranges>` per chunk file with deleted anchors (section omitted
      when empty). `<ranges>` = sorted lines compressed as `12-18, 40`.
    - previous findings of the chunk's files are included in **every** mode (was incremental only),
      as JSON of `{"id","file","line","title","severity"}` under the existing header.
  - `render`: the previous section shows whenever `review["previous"]` is non-empty (drop the
    `mode == "incremental"` condition); line numbers are the relocated ones.
  - `agent/pr-reviewer.md` rule 1 becomes: 프롬프트에 "검토 대상 줄"이 있으면 그 줄에만 지적한다
    (없으면 diff의 `+` 라인). 삭제 때문에 생긴 문제는 "삭제 지점" 줄에 지적한다.
    Add rule: 확인하지 못한 부분이 있으면 `incomplete_reason`에 이유를 적는다.

- [ ] **Step 1: Write the failing unit tests**

```python
# tests/test_prompt.py
def test_incremental_lists_focus_lines(self):
    ctx = dict(CTX, mode="incremental", added={"a.cu": {3, 4, 5, 9}}, deleted={"a.cu": {12}})
    p = build_prompt(CHUNK, ctx)
    self.assertIn("검토 대상 줄:\n- a.cu: 3-5, 9", p)
    self.assertIn("삭제 지점:\n- a.cu: 12", p)
def test_previous_included_in_full_mode(self):
    ctx = dict(CTX, mode="full", previous=[{"id": "f1", "file": "a.cu", "line": 3, "title": "t",
                                            "severity": "major", "pre": "ask", "located": True}])
    p = build_prompt(CHUNK, ctx)
    self.assertIn('"id": "f1"', p); self.assertNotIn('"pre"', p)
# tests/test_schema_agent.py: assert "검토 대상 줄", "삭제 지점", "incomplete_reason" appear in the agent text
# tests/test_render.py: previous section rendered in full mode when previous is non-empty
```

- [ ] **Step 2: Write `tests/test_integration_force.py`** (each starts with the "blocking" first run on
  C1 = `src/k.cu` BUGGY + `src/u.cu`; helper `force(files, drop=())` = `git reset -q --soft BASE`, write
  `files`, `git rm -q --cached` + delete each path in `drop`, commit, set `self.gh.head`, return the
  new head; scenario `silent` comes from Task 1)

```python
def test_squash_no_llm_call_nothing_posted(self):
    new = self.force({})                       # same tree, new sha
    calls = len(self.fake_calls())
    self.assertEqual(self.run_main("silent", "--repo", self.R, "--pr", "1"), 0)
    self.assertEqual((len(self.fake_calls()), len(self.gh.posted)), (calls, 1))
    self.assertIn("새로 리뷰할 변경 없음", self.stdout)
def test_rebase_shift_keeps_block_same_id(self):
    first_id = parse_state(self.gh.posted[0]["body"])["findings"][0]["id"]
    self.force({"src/k.cu": "// pad\n" * 10 + BUGGY})
    self.assertEqual(self.run_main("silent", "--repo", self.R, "--pr", "1"), 0)
    st = parse_state(self.gh.posted[-1]["body"])
    self.assertEqual(self.gh.posted[-1]["event"], "REQUEST_CHANGES")
    self.assertEqual([(f["id"], f["line"]) for f in st["findings"]], [(first_id, 12)])
def test_fix_resolved_approves_and_dismisses(self):   # force FIXED, scenario "resolved" -> APPROVE, dismissed [1]
def test_anchor_changed_model_silent_comment(self):
    self.force({"src/k.cu": BUGGY.replace("p[1]", "p[2]")})
    self.assertEqual(self.run_main("silent", "--repo", self.R, "--pr", "1"), 0)
    self.assertEqual(self.gh.posted[-1]["event"], "COMMENT")
    self.assertIn("이전 차단 지적 확인 필요", self.gh.posted[-1]["body"])
    self.assertEqual(self.gh.dismissed, [])
def test_file_dropped_resolves_and_approves(self):    # force({}, drop=["src/k.cu"]) -> no LLM call, APPROVE
def test_merge_main_no_review(self):                  # main gains m.py; merge into PR; base = new main -> nothing posted
def test_deletion_reviewed(self):                     # C2 deletes a line of u.cu -> fake call stdin has "삭제 지점"
```

`FakeGitHub` must report the new base sha for the merge test (set `self.gh.base`); add that
attribute to `tests/fakes/fake_github.py` if it is not already settable.

- [ ] **Step 3: Run** `python3 -m unittest tests.test_prompt tests.test_schema_agent tests.test_render tests.test_integration_force` — Expected: FAIL.
- [ ] **Step 4: Implement** prompt/render/agent/cli changes (`ctx` gets `added`, `deleted`,
  `previous = scope["previous"]`).
- [ ] **Step 5: Update `docs/runbook.md`**: replace the incremental-review description with:
  content-based detection (squash/rebase/merge cost no LLM call), "nothing posted when no new
  lines", COMMENT cases (partial chunk, skipped files, unclear previous blocker), and the
  trade-offs from spec 6.3/6.5 (whole-file reformat re-reviews the file).
- [ ] **Step 6: Run** the full suite — Expected: all OK.
- [ ] **Step 7: Commit** `feat: focus prompts on new lines and cover force-push flows`
