# Plan Part 2 — Fingerprints and Hunk Selection

Read `00-overview.md` first. Spec sections 6.1–6.3.

### Task 2: `review/fingerprint.py` (pure functions)

**Files:**
- Create: `review/fingerprint.py`
- Test: `tests/test_fingerprint.py`

**Interfaces:**
- Produces (all take raw, un-normalized text; each function applies `norm` itself):
  - `SEEN_CAP = 24000`
  - `norm(s: str) -> str` — `s.rstrip()`.
  - `fp_line(lines: List[str], n: int) -> bytes` — `lines` are head file lines, `n` 1-based;
    `sha1((A + "\n" + L + "\n" + B).encode("utf-8")).digest()[:4]` with A/L/B = normalized lines
    `n-1`, `n`, `n+1` ("" when out of range).
  - `fp_removed(lines: List[str], pos: int, text: str) -> bytes` — `pos` = head line number just
    below the deletion point; `sha1(("-\n" + A + "\n" + norm(text) + "\n" + B).encode()).digest()[:4]`
    with A = line `pos-1`, B = line `pos` ("" when out of range).
  - `anchor_line(lines: List[str], pos: int) -> int` — `pos` if `pos <= len(lines)` else `max(len(lines), 1)`.
  - `pack(c: Counter) -> str` — base64 (ascii) of `b"".join(sorted(c.elements()))`.
  - `unpack(s: str) -> Counter` — inverse; invalid base64, non-str or length not a multiple of 4 → `Counter()`.
  - `whole_hash(c: Counter) -> bytes` — `sha1(b"".join(sorted(c.elements()))).digest()[:4]`.
  - `encode_seen(seen: Dict[str, Counter], cap: int = SEEN_CAP) -> Tuple[Dict[str, str], Dict[str, str]]`
    — returns `(seen_raw, seen_files_raw)`. Empty counters are omitted. While the summed length of
    `seen_raw` values exceeds `cap`, move the path with the longest packed value (ties: larger path
    string first) into `seen_files_raw` as `base64(whole_hash(counter))`.
  - `decode_seen(state: Optional[dict]) -> Tuple[Dict[str, Counter], Dict[str, bytes]]` — from
    `state["seen"]` / `state["seen_files"]`; anything malformed is skipped; `None` → `({}, {})`.

- [ ] **Step 1: Write `tests/test_fingerprint.py`**

```python
L = ["int k(int *p) {", "  return p[1];", "}"]
def test_window_changes_with_neighbor(self):
    self.assertNotEqual(fp_line(L, 2), fp_line(["int j(int *p) {"] + L[1:], 2))
def test_window_same_when_shifted(self):
    self.assertEqual(fp_line(L, 2), fp_line(["// x"] * 10 + L, 12))
def test_crlf_same_print(self):
    self.assertEqual(fp_line([s + "\r" for s in L], 2), fp_line(L, 2))
def test_indent_matters(self):
    self.assertNotEqual(fp_line(["if x:", "    y()", ""], 2), fp_line(["if x:", "y()", ""], 2))
def test_edges_use_empty_neighbors(self):
    self.assertEqual(len(fp_line(L, 1)), 4); self.assertEqual(len(fp_line(L, 3)), 4)
def test_removed_differs_from_added(self):
    self.assertNotEqual(fp_removed(L, 2, "  sync();"), fp_line(L, 2))
def test_removed_depends_on_deletion_point(self):
    self.assertNotEqual(fp_removed(L, 2, "x"), fp_removed(L, 3, "x"))
def test_anchor_line_at_eof(self):
    self.assertEqual((anchor_line(L, 2), anchor_line(L, 4), anchor_line([], 1)), (2, 3, 1))
def test_pack_roundtrip_keeps_counts(self):
    c = Counter({b"abcd": 2, b"wxyz": 1})
    self.assertEqual(unpack(pack(c)), c)
def test_unpack_corrupt_is_empty(self):
    for bad in ["!!!", "YWJj", None, 5]:          # "YWJj" = 3 bytes
        self.assertEqual(unpack(bad), Counter())
def test_encode_under_cap_keeps_lines(self):
    raw, files = encode_seen({"a": Counter({b"abcd": 1})})
    self.assertEqual((list(raw), files), (["a"], {}))
def test_encode_over_cap_moves_largest_file(self):
    big = Counter({i.to_bytes(4, "big"): 1 for i in range(3000)})   # ~16k chars
    small = Counter({b"abcd": 1})
    raw, files = encode_seen({"big": big, "small": small}, cap=1000)
    self.assertEqual((list(raw), list(files)), (["small"], ["big"]))
    self.assertEqual(decode_seen({"seen": raw, "seen_files": files})[1]["big"], whole_hash(big))
def test_decode_none_and_garbage(self):
    self.assertEqual(decode_seen(None), ({}, {}))
    self.assertEqual(decode_seen({"seen": {"a": 1}, "seen_files": "x"}), ({}, {}))
```

- [ ] **Step 2: Run** `python3 -m unittest tests.test_fingerprint` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement `review/fingerprint.py`** with `hashlib`, `base64` (`b64decode(s, validate=True)`), `collections.Counter`.
- [ ] **Step 4: Run** the full suite — Expected: all OK.
- [ ] **Step 5: Commit** `feat: add line fingerprints and seen-set codec`

---

### Task 3: `review/hunks.py` and `gitutil.read_blobs`

**Files:**
- Create: `review/hunks.py`
- Modify: `review/gitutil.py` (add `read_blobs`), `review/diff_split.py` (rename `_split_hunks` →
  `split_hunks`; extract `reviewable(f, cfg) -> bool` from the `build_chunks` filter — net line
  count must not grow)
- Test: `tests/test_hunks.py`, `tests/test_gitutil.py`

**Interfaces:**
- Consumes: Task 2 functions; `parse_unified_diff`, `split_hunks` (diff_split).
- Produces:
  - `gitutil.read_blobs(repo: str, rev: str, paths: List[str]) -> Dict[str, List[str]]` — one
    `git cat-file --batch` process, stdin `"<rev>:<path>\n"` per path; output lines are the file
    split on `"\n"` (a trailing empty element from a final newline dropped), decoded UTF-8 with
    `errors="replace"`; missing objects are omitted; timeout 120 s → `GitError`.
  - `hunks.scan_hunk(hunk: str, lines: List[str]) -> List[Change]` — new-side counter starts at
    the `+c` of the `@@` header; `+` → Change("+", n, fp_line(lines, n)), n += 1; ` ` → n += 1;
    `-` → Change("-", anchor_line(lines, n), fp_removed(lines, n, text)); any other line
    (`\ No newline…`, `[truncated by ai-review]`, empty) is ignored.
  - `hunks.select_file(f: FileDiff, lines: List[str], seen: Optional[Counter], whole: Optional[bytes]) -> FileSelection`
    — `head` = Counter of all change prints. Effective seen = `head` if `whole == whole_hash(head)`,
    else `seen or Counter()`. A print is new when `head[p] > eff[p]`; every change with a new print is
    new. Kept hunks = hunks with at least one new change; `file` = `parse_unified_diff(header +
    "".join(kept))[0]` or `None`; `added`/`deleted` = lines of new `+`/`-` changes.
  - `hunks.diff_prints(diff: str, blobs: Dict[str, List[str]]) -> Dict[str, Counter]` — for a
    (chunk) diff text containing one or more `diff --git` sections, the Counter of change prints per
    path (paths missing from `blobs` are skipped).

- [ ] **Step 1: Write the failing tests** (`tests/test_hunks.py` builds diff text literally;
  `tests/test_gitutil.py` uses the existing temp-repo helpers)

```python
HEAD = ["a", "b", "NEW", "c", "d"]
DIFF = ("diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
        "@@ -1,4 +1,5 @@\n a\n b\n+NEW\n c\n d\n")
DEL = ("diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
       "@@ -1,5 +1,4 @@\n a\n b\n-sync()\n c\n d\n")
def test_scan_added(self):
    ch = scan_hunk(split_hunks(DIFF)[1][0], HEAD)
    self.assertEqual([(c["kind"], c["line"]) for c in ch], [("+", 3)])
def test_scan_deleted_anchor(self):
    ch = scan_hunk(split_hunks(DEL)[1][0], ["a", "b", "c", "d"])
    self.assertEqual([(c["kind"], c["line"]) for c in ch], [("-", 3)])
def test_no_newline_marker_ignored(self): ...  # hunk ending "+x\n\\ No newline at end of file\n" -> one change, right line
def test_first_review_keeps_everything(self):
    s = select_file(parse_unified_diff(DIFF)[0], HEAD, None, None)
    self.assertEqual((s["added"], s["file"]["path"]), ({3}, "f.py"))
def test_seen_hunk_dropped(self):
    head = select_file(parse_unified_diff(DIFF)[0], HEAD, None, None)["head"]
    s = select_file(parse_unified_diff(DIFF)[0], HEAD, head, None)
    self.assertIsNone(s["file"]); self.assertEqual(s["added"], set())
def test_deletion_only_hunk_is_target(self):
    s = select_file(parse_unified_diff(DEL)[0], ["a", "b", "c", "d"], Counter(), None)
    self.assertEqual((s["deleted"], s["added"]), ({3}, set()))
def test_duplicate_block_count_rises(self):
    # head has the reviewed block twice; seen holds one copy -> both copies new
def test_whole_hash_match_means_seen(self): ...
def test_diff_prints_two_files(self): ...
def test_crlf_blob_matches_diff(self): ...  # blob lines end with "\r", diff "+" text too -> same prints as LF
# tests/test_gitutil.py
def test_read_blobs(self):   # two files + one missing path; non-UTF-8 byte decodes with U+FFFD
```

- [ ] **Step 2: Run** `python3 -m unittest tests.test_hunks tests.test_gitutil` — Expected: FAIL.
- [ ] **Step 3: Implement** `read_blobs` (parse `"<sha> blob <size>\n"` headers and read exactly
  `size` bytes plus the trailing `\n` from a bytes stdout; `"<name> missing"` → skip),
  `review/hunks.py`, and the `diff_split` rename/extraction.
- [ ] **Step 4: Run** the full suite — Expected: all OK.
- [ ] **Step 5: Commit** `feat: select new hunks by fingerprint and read head blobs`
