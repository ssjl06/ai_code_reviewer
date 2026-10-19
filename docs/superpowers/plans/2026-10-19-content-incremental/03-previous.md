# Plan Part 3 — Previous Findings by Anchor

Read `00-overview.md` first. Spec section 6.4.

### Task 4: `review/previous.py` and anchor-based ids in `aggregate.py`

**Files:**
- Create: `review/previous.py`
- Modify: `review/aggregate.py` (`finding_id`, `merge`, `_previous`)
- Test: `tests/test_previous.py`, `tests/test_aggregate.py`

**Interfaces:**
- Consumes: `fingerprint.fp_line` (Task 2); `merge` signature from Task 1.
- Produces:
  - `previous.relocate(prev: List[dict], pr_paths: Set[str], blobs: Dict[str, List[str]], targets: Set[str], renames: Dict[str, str]) -> List[PrevEntry]`
    — for each state finding (copied):
    1. `file = renames.get(file, file)`.
    2. File not in `pr_paths` → `pre="resolved"`, `note="파일이 PR에서 빠짐"`, `located=False`.
    3. Else search `blobs.get(file, [])` for lines whose `fp_line(...).hex() == anchor` (empty anchor
       never matches). Found → `located=True`, `line` = candidate closest to the old line (ties: the
       smaller). Not found → `located=False`, line unchanged.
    4. `pre = "ask"` if `file in targets` else `"open"`.
  - `previous.anchor_of(blobs: Dict[str, List[str]]) -> Callable[[str, int], str]` — returns
    `fp_line(blobs[path], line).hex()`, or `""` when the path is not in `blobs`.
  - `aggregate.finding_id(f) -> str` — sha1 of `"%s:%s:%s" % (file, f.get("anchor") or line, title)`,
    first 8 hex chars (findings without an anchor keep the old behaviour).
  - `aggregate.merge(results, cfg, previous, changed, delta_files, no_targets, skipped=None, anchor=None)`:
    - when `anchor` is given, each kept finding gets `f["anchor"] = anchor(f["file"], f["line"])`
      before `finding_id`;
    - a new finding is dropped as a re-report when its id is a previous id (unchanged) **or** its
      non-empty `(file, anchor)` equals a previous entry's `(file, anchor)`;
    - `_previous` resolution for an entry the model did not report:
      `pre == "resolved"` → `resolved` (note kept); `pre == "open"` → `open`;
      `pre == "ask"` → `open` if `located` else `unclear`;
      entry without `pre` → the current rule (`unclear` if file in `delta_files` else `open`).
      Task 5 removes `delta_files` and this fallback.
    - a model-reported status still wins over `pre` (worst status rule unchanged).

- [ ] **Step 1: Write `tests/test_previous.py`**

```python
LINES = ["int k(int *p) {", "  return p[1];", "}"]
A = fp_line(LINES, 2).hex()
def E(**kw):
    e = {"id": "f1", "file": "src/k.cu", "line": 2, "anchor": A, "title": "t",
         "severity": "major", "blocking": True}
    e.update(kw); return e
def test_file_dropped_resolved(self):
    r = relocate([E()], set(), {}, set(), {})[0]
    self.assertEqual((r["pre"], r["note"]), ("resolved", "파일이 PR에서 빠짐"))
def test_shifted_line_relocated(self):
    blobs = {"src/k.cu": ["//"] * 10 + LINES}
    r = relocate([E()], {"src/k.cu"}, blobs, set(), {})[0]
    self.assertEqual((r["line"], r["located"], r["pre"]), (12, True, "open"))
def test_target_file_asks_model(self):
    r = relocate([E()], {"src/k.cu"}, {"src/k.cu": LINES}, {"src/k.cu"}, {})[0]
    self.assertEqual((r["pre"], r["located"]), ("ask", True))
def test_changed_anchor_not_located(self):
    blobs = {"src/k.cu": ["int k(int *p) {", "  return p[0];", "}"]}
    r = relocate([E()], {"src/k.cu"}, blobs, {"src/k.cu"}, {})[0]
    self.assertEqual((r["located"], r["line"]), (False, 2))
def test_closest_duplicate_wins(self): ...  # anchor occurs at lines 2 and 9, old line 8 -> 9
def test_rename_carries_finding(self):
    r = relocate([E(file="old.cu")], {"src/k.cu"}, {"src/k.cu": LINES}, set(), {"old.cu": "src/k.cu"})[0]
    self.assertEqual((r["file"], r["pre"], r["located"]), ("src/k.cu", "open", True))
def test_anchor_of(self):
    fn = anchor_of({"src/k.cu": LINES})
    self.assertEqual((fn("src/k.cu", 2), fn("x", 1)), (A, ""))
```

- [ ] **Step 2: Add to `tests/test_aggregate.py`** (helper `run(..., anchor=None)` passes it through;
  `P(..., anchor="", pre=None, located=False)` adds those keys only when given):

```python
def test_id_survives_line_shift(self):
    fn = lambda path, line: "aaaa0000"
    a = run([res(0, [F(line=10)])], anchor=fn)["findings"][0]["id"]
    b = run([res(0, [F(line=30)])], anchor=fn)["findings"][0]["id"]
    self.assertEqual(a, b)
def test_rereport_same_anchor_other_title_dropped(self):
    r = run([res(0, [F(title="other")])], [P(anchor="aaaa0000", pre="ask", located=True)],
            anchor=lambda p, l: "aaaa0000")
    self.assertEqual(r["findings"], [])
def test_pre_resolved_without_report(self):
    r = run([], [P(blocking=True, pre="resolved")])
    self.assertEqual((r["previous"][0]["status"], r["event"]), ("resolved", "APPROVE"))
def test_pre_ask_located_unreported_stays_open_blocking(self):
    r = run([res(0, [])], [P(blocking=True, pre="ask", located=True)])
    self.assertEqual((r["previous"][0]["status"], r["event"]), ("open", "REQUEST_CHANGES"))
def test_pre_ask_not_located_is_unclear(self):
    r = run([res(0, [])], [P(blocking=True, pre="ask", located=False)])
    self.assertEqual((r["previous"][0]["status"], r["event"]), ("unclear", "COMMENT"))
def test_model_report_beats_pre(self):
    r = run([res(0, [], prev=[("f1", "resolved")])], [P(blocking=True, pre="ask", located=True)])
    self.assertEqual(r["previous"][0]["status"], "resolved")
```

`run([], ...)` with no results: `no_targets` stays False; verify `incomplete == []` so the
verdict is APPROVE (a run that only resolves previous findings deterministically).

- [ ] **Step 3: Run** `python3 -m unittest tests.test_previous tests.test_aggregate` — Expected: new tests FAIL.
- [ ] **Step 4: Implement.** Keep `aggregate.py` ≤ ~165 lines; if it would exceed, move
  `_previous` into `review/previous.py` as `resolve(results, previous, delta_files)`.
- [ ] **Step 5: Run** the full suite — Expected: all OK.
- [ ] **Step 6: Commit** `feat: relocate previous findings by content anchor`
