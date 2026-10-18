# Plan Part 1 — Config and Diff Splitting

Read `00-overview.md` first (Global Constraints, Shared Data Shapes).

### Task 1: Config loader and repository skeleton

**Files:**
- Create: `config/defaults.json`, `review/__init__.py`, `review/hooks/__init__.py`, `review/config.py`, `.gitignore`
- Test: `tests/__init__.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `load_config(path: str | None = None, overrides: dict | None = None) -> dict`
  returning a `Config`; `KIT_DIR: str` (absolute path of the kit root, parent of `review/`).

- [ ] **Step 1: Write the failing tests** in `tests/test_config.py`

```python
class ConfigTest(unittest.TestCase):
    def test_defaults_have_spec_values(self):
        c = load_config()
        self.assertEqual(c["chunk_max_tokens"], 20000)
        self.assertEqual(c["max_chunks"], 6)
        self.assertEqual(c["concurrency"], 3)
        self.assertEqual(c["max_turns"], 10)
        self.assertEqual(c["explore_budget"], 6)
        self.assertEqual(c["deadline_s"], 1200)
        self.assertEqual(c["block_severities"], ["critical", "major"])
        self.assertEqual(c["block_confidence"], 80)
        self.assertEqual(c["min_confidence"], 50)

    def test_file_and_overrides_merge(self):
        # temp JSON file {"concurrency": 2}; overrides {"max_chunks": 4}
        c = load_config(tmp_path, {"max_chunks": 4})
        self.assertEqual((c["concurrency"], c["max_chunks"]), (2, 4))
        self.assertEqual(c["max_turns"], 10)

    def test_unknown_key_rejected(self):
        with self.assertRaises(KeyError):
            load_config(None, {"max_turn": 5})
```

- [ ] **Step 2: Run** `python3 -m unittest tests.test_config -v` — Expected: FAIL (`ModuleNotFoundError: review.config`).
- [ ] **Step 3: Implement** `config/defaults.json` with exactly the `Config` keys/values from the overview, and
  `review/config.py`: read defaults, shallow-merge file then overrides; any key not in defaults raises
  `KeyError(key)`. `.gitignore`: `__pycache__/`, `*.pyc`, `.ai-review-out/`.
- [ ] **Step 4: Run** `python3 -m unittest discover -s tests -v` — Expected: 3 tests OK.
- [ ] **Step 5: Commit** `feat: add config loader and defaults`

---

### Task 2: Git helpers and diff splitting

**Files:**
- Create: `review/gitutil.py`, `review/diff_split.py`
- Test: `tests/test_gitutil.py`, `tests/test_diff_split.py`, `tests/fixtures/sample.diff`

**Interfaces:**
- Consumes: `Config` keys `chunk_max_tokens`, `max_chunks`, `exclude_globs`.
- Produces:
  - `gitutil.git(repo: str, *args: str, timeout: int = 60) -> str` — runs
    `git -c core.quotepath=false <args>` in `repo`, returns stdout, raises `GitError(stderr)` on non-zero.
  - `gitutil.is_ancestor(repo, ancestor: str, head: str) -> bool` (`merge-base --is-ancestor`, False on unknown sha).
  - `gitutil.diff_text(repo, base: str, head: str, merge_base: bool) -> str` — `git diff --no-color
    --no-ext-diff -M -U3 base...head` when `merge_base` else `base..head`.
  - `diff_split.parse_unified_diff(text: str) -> list[FileDiff]`
  - `diff_split.estimate_tokens(text: str) -> int` = `math.ceil(len(text) / 3.5)`
  - `diff_split.priority(path: str) -> int` — 0 source, 1 build/CI, 2 tests, 3 docs, 4 other.
  - `diff_split.build_chunks(files: list[FileDiff], cfg: dict) -> tuple[list[Chunk], list[str]]`
    (chunks, skipped paths).
  - `diff_split.line_map(files, key: str) -> dict[str, set[int]]` with key `"added_lines"` or `"right_lines"`.

- [ ] **Step 1: Write `tests/test_gitutil.py`** using a temp repo created with `git init` + two commits:

```python
def test_diff_text_two_and_three_dot(self):
    self.assertIn("+second", diff_text(self.repo, self.c1, self.c2, merge_base=True))
def test_is_ancestor(self):
    self.assertTrue(is_ancestor(self.repo, self.c1, self.c2))
    self.assertFalse(is_ancestor(self.repo, self.c2, self.c1))
    self.assertFalse(is_ancestor(self.repo, "0" * 40, self.c2))
def test_git_error_raises(self):
    with self.assertRaises(GitError):
        git(self.repo, "show", "no-such-ref")
```

- [ ] **Step 2: Write `tests/test_diff_split.py`** (fixture `sample.diff`: modified `src/k.cu` with two hunks,
  added `docs/a.md`, deleted `old.py`, binary `img.png`, renamed `x.h -> y.h`, file `vendor/z.c`):

```python
def test_parse_statuses_and_lines(self):
    by = {f["path"]: f for f in parse_unified_diff(SAMPLE)}
    self.assertEqual(by["src/k.cu"]["status"], "M")
    self.assertEqual(by["old.py"]["status"], "D")
    self.assertTrue(by["img.png"]["binary"])
    self.assertEqual((by["y.h"]["status"], by["y.h"]["old_path"]), ("R", "x.h"))
    self.assertEqual(by["src/k.cu"]["added_lines"], [11, 12, 40])   # values from fixture
    self.assertIn(10, by["src/k.cu"]["right_lines"])                # context line

def test_quoted_and_unicode_paths(self):
    files = parse_unified_diff(QUOTED)   # 'diff --git "a/my dir/한글.py" "b/my dir/한글.py"'
    self.assertEqual(files[0]["path"], "my dir/한글.py")

def test_priority_order(self):
    self.assertEqual([priority(p) for p in
        ["a.cu", "ci.yml", "tests/test_a.py", "README.md", "LICENSE"]], [0, 1, 2, 3, 4])

def test_excluded_deleted_binary_not_chunked(self):
    chunks, skipped = build_chunks(parse_unified_diff(SAMPLE), CFG)
    names = [p for c in chunks for p in c["files"]]
    self.assertNotIn("vendor/z.c", names); self.assertNotIn("old.py", names)
    self.assertNotIn("img.png", names)
    self.assertIn("src/k.cu", chunks[0]["files"])     # source first

def test_chunk_limit_and_cap(self):
    cfg = dict(CFG, chunk_max_tokens=1000, max_chunks=2)
    chunks, skipped = build_chunks(make_files(n=5, tokens_each=800), cfg)
    self.assertEqual(len(chunks), 2)
    self.assertTrue(all(c["est_tokens"] <= 1000 for c in chunks))
    self.assertEqual(len(skipped), 3)

def test_large_file_split_by_hunks(self):
    chunks, _ = build_chunks([make_file(hunks=4, tokens_each_hunk=600)], dict(CFG, chunk_max_tokens=1300))
    self.assertEqual(len(chunks), 2)          # 2 hunks per chunk, each with the file header

def test_oversized_single_hunk_truncated(self):
    chunks, _ = build_chunks([make_file(hunks=1, tokens_each_hunk=5000)], dict(CFG, chunk_max_tokens=1000))
    self.assertEqual(len(chunks), 1)
    self.assertLessEqual(chunks[0]["est_tokens"], 1000)
    self.assertIn("[truncated by ai-review]", chunks[0]["diff"])

def test_related_files_grouped(self):
    chunks, _ = build_chunks(make_named(["a/foo.cu", "b/bar.py", "a/foo.cuh"]), CFG)
    self.assertEqual(chunks[0]["files"][:2], ["a/foo.cu", "a/foo.cuh"])
```

`make_file`/`make_files`/`make_named` are test helpers that synthesize `FileDiff` dicts of a target size.

- [ ] **Step 3: Run** `python3 -m unittest tests.test_gitutil tests.test_diff_split -v` — Expected: FAIL (import errors).
- [ ] **Step 4: Implement `review/gitutil.py`** with `subprocess.run(..., capture_output=True, text=True, timeout=timeout)`.
- [ ] **Step 5: Implement `review/diff_split.py`.** Parsing: split on `^diff --git `; unquote C-style quoted
  paths (`"a/..."` with octal escapes → decode as UTF-8 bytes); status from `new file`/`deleted file`/
  `rename from|to`/`Binary files`; track new-side line numbers from `@@ -a,b +c,d @@`.
  Chunking algorithm:

```text
targets = [f for f in files if not binary, status != "D", not fnmatch(any exclude_glob)]
sort targets by (priority(path), dirname(path), stem(path), path)   # keeps foo.cu next to foo.cuh
units = for each target: whole file if est <= limit, else per-hunk units (header + hunk);
        a single hunk > limit is cut to fit and suffixed "\n[truncated by ai-review]\n"
greedy pack units in order into chunks with est_tokens <= limit
chunks beyond max_chunks -> their file paths go to skipped (deduplicated, order kept)
```

- [ ] **Step 6: Run** `python3 -m unittest discover -s tests -v` — Expected: all OK.
- [ ] **Step 7: Commit** `feat: add git helpers and diff chunking`
