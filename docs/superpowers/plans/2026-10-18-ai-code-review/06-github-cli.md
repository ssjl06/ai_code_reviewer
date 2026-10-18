# Plan Part 6 — GitHub Client and CLI

Read `00-overview.md` first. Spec sections 3.3, 6, 8, 9.3.

### Task 10: GitHub REST client

**Files:**
- Create: `review/github_api.py`
- Test: `tests/test_github_api.py`

**Interfaces:**
- Consumes: `parse_state` (Task 9).
- Produces: `class GitHubError(Exception)` with `.status: int`, `.message: str`; `class GitHub`:
  - `__init__(self, api_url: str, token: str, repo: str, pr: int, opener=urllib.request.urlopen)`
  - `request(self, method: str, path: str, body: dict | None = None) -> Any` — headers
    `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`; timeout 30 s; non-2xx → `GitHubError`.
  - `get_pr(self) -> dict`
  - `list_reviews(self) -> list[dict]` — follows `?per_page=100&page=N` until a short page.
  - `latest_state(self, bot_login: str) -> dict | None` — newest-first (by `id`) review whose
    `user.login` equals `bot_login` case-insensitively and whose body parses.
  - `dismiss_change_requests(self, bot_login: str, message: str) -> int` — PUT
    `/pulls/{n}/reviews/{id}/dismissals` `{"message", "event": "DISMISS"}` for bot reviews in `CHANGES_REQUESTED`.
  - `post_review(self, commit_sha, event, body, comments: list[dict]) -> dict` — POST `/pulls/{n}/reviews`;
    comment dict `{"path", "line", "side": "RIGHT", "body"}`.
  - `post_with_fallback(self, commit_sha, event, body, comments, fallback_body) -> dict` — on `GitHubError` 422
    retry once with `fallback_body` and no comments; if the message contains `own pull request`, the retry uses
    event `COMMENT`. A second failure propagates.

- [ ] **Step 1: Write `tests/test_github_api.py`** with a `FakeOpener` that records `Request` objects and
  returns queued `(status, json)` responses (raising `urllib.error.HTTPError` for >= 400).

```python
def test_auth_header_and_path(self):
    gh, op = make(responses=[(200, {"number": 7})]); gh.get_pr()
    req = op.requests[0]
    self.assertEqual(req.get_header("Authorization"), "Bearer T")
    self.assertTrue(req.full_url.endswith("/repos/o/r/pulls/7"))
def test_pagination(self):
    gh, op = make(responses=[(200, [rev(i) for i in range(100)]), (200, [rev(100)])])
    self.assertEqual(len(gh.list_reviews()), 101)
def test_bot_login_case_insensitive(self):
    gh, _ = make(responses=[(200, [rev(1, login="AI-Bot", body=MARKED), rev(2, login="human", body=MARKED)])])
    self.assertEqual(gh.latest_state("ai-bot")["sha"], "abc")
def test_latest_state_skips_corrupt(self): ...   # newest bot review corrupt, older valid -> older
def test_dismiss_only_change_requests(self):
    gh, op = make(responses=[(200, [rev(1, state="CHANGES_REQUESTED"), rev(2, state="APPROVED")]), (200, {})])
    self.assertEqual(gh.dismiss_change_requests("ai-bot", "m"), 1)
    self.assertEqual(op.requests[1].get_method(), "PUT")
def test_422_fallback_moves_comments(self):
    gh, op = make(responses=[(422, {"message": "line must be part of the diff"}), (200, {"id": 9})])
    gh.post_with_fallback("sha", "APPROVE", "b", [C()], "fb")
    sent = json.loads(op.requests[1].data)
    self.assertEqual((sent["body"], sent["comments"], sent["event"]), ("fb", [], "APPROVE"))
def test_own_pr_approve_falls_back_to_comment(self): ...   # 422 "Can not approve your own pull request" -> COMMENT
def test_second_failure_raises(self): ...
```

- [ ] **Step 2: Run** `python3 -m unittest tests.test_github_api -v` — Expected: FAIL.
- [ ] **Step 3: Implement `review/github_api.py`** with `urllib.request.Request` and `json`.
- [ ] **Step 4: Run** `python3 -m unittest discover -s tests -v` — Expected: all OK.
- [ ] **Step 5: Commit** `feat: add GitHub review client`

---

### Task 11: Review scope, CLI entry point, integration test

**Files:**
- Create: `review/scope.py`, `review/cli.py`, `review/__main__.py` (calls `cli.main`)
- Test: `tests/test_scope.py`, `tests/test_integration.py`, `tests/fakes/fake_github.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `scope.determine(repo: str, base: str, head: str, prev: State | None) -> dict` returning
    `{"mode": "full|incremental|unchanged", "range": str, "targets": list[FileDiff],
    "pr_files": list[FileDiff], "changed": dict | None, "delta_files": set | None}`:
    `unchanged` if `prev.sha == head`; `incremental` if `is_ancestor(prev.sha, head)` (targets = delta
    `prev.sha..head`); otherwise `full` (targets = `base...head`). `pr_files` is always `base...head`.
  - `cli.main(argv: list[str] | None = None) -> int`. Options: `--repo` (default `.`), `--base`, `--head`,
    `--pr` (int), `--dry-run`, `--config`, `--set key=json` (repeatable overrides), `--out-dir`
    (default `$RUNNER_TEMP/ai-review` or `./.ai-review-out`), `--state-file` (dry-run previous state).

`cli.main` flow (log lines in Korean, each prefixed `[main]`):

```text
cfg, deadline = load_config(...), monotonic() + deadline_s; work dir = --out-dir (runner paths["work"])
online: gh from env (GITHUB_API_URL, GITHUB_REPOSITORY, AI_REVIEW_BOT_PAT, --pr); pr = gh.get_pr();
        base/head default to pr base/head sha; prev = gh.latest_state(AI_REVIEW_BOT_NAME)
dry-run: base/head from args (required); prev from --state-file or None
scope = determine(...); "unchanged" -> log "이미 리뷰한 커밋" and return 0
chunks, skipped = build_chunks(scope.targets, cfg); prompts = build_prompt per chunk
results = run_all(...) unless no chunks
review = merge(results, cfg, prev.findings or [], scope.changed, scope.delta_files, no_targets=not chunks)
inline, body_only = split_inline(review.findings, line_map(scope.pr_files, "right_lines"))
state = {"sha": head, "findings": new findings + previous with status open/unclear}
body = render_body(review, body_only, meta, state); fallback = render_body(review, inline + body_only, meta, state)
append render_step_summary(results) to $GITHUB_STEP_SUMMARY if set
dry-run: print {"event","body","comments"} JSON and write <out-dir>/review.json; return 0
online: dismiss bot change requests unless REQUEST_CHANGES; post_with_fallback(...)
return 1 if review.failed_all or posting raised GitHubError, else 0
```

- [ ] **Step 1: Write `tests/test_scope.py`** (temp repo: base → c1 → c2; c2' rewritten on base):

```python
def test_full_without_prev(self):   self.assertEqual(determine(R, BASE, C2, None)["mode"], "full")
def test_unchanged(self):           self.assertEqual(determine(R, BASE, C2, {"sha": C2, "findings": []})["mode"], "unchanged")
def test_incremental(self):
    s = determine(R, BASE, C2, {"sha": C1, "findings": []})
    self.assertEqual((s["mode"], s["range"]), ("incremental", f"{C1[:7]}..{C2[:7]}"))
    self.assertEqual({f["path"] for f in s["targets"]}, {"only_in_c2.py"})
    self.assertIn("only_in_c1.py", {f["path"] for f in s["pr_files"]})
def test_force_push_falls_back_to_full(self):
    self.assertEqual(determine(R, BASE, C2_REWRITTEN, {"sha": C1, "findings": []})["mode"], "full")
```

- [ ] **Step 2: Write `tests/fakes/fake_github.py`**: `http.server` on `127.0.0.1:0` in a thread; serves
  `GET /repos/o/r/pulls/1` (base/head from the test), `GET .../reviews` (from its stored list),
  `POST .../reviews` (stores with `user.login="ai-bot"`, increasing ids, state from event), `PUT .../dismissals`;
  records every request.
- [ ] **Step 3: Write `tests/test_integration.py`** (env: fake claude on PATH via `--set claude_bin=...`,
  fake GitHub URL, `AI_REVIEW_BOT_NAME=ai-bot`, LLM URL/key dummies):

```python
def test_first_full_then_incremental(self):
    self.assertEqual(main(["--repo", R, "--pr", "1"]), 0)           # scenario "blocking": 1 major/85 finding
    first = self.gh.posted[-1]
    self.assertEqual(first["event"], "REQUEST_CHANGES")
    self.assertEqual(parse_state(first["body"])["sha"], C1)
    self.assertEqual(first["comments"][0]["path"], "src/k.cu")
    # C2 edits src/k.cu (the fix) and adds docs/n.md; scenario "resolved": previous f1 resolved
    self.add_commit({"src/k.cu": FIXED, "docs/n.md": "x\n"}); self.gh.head = C2
    self.assertEqual(main(["--repo", R, "--pr", "1"]), 0)
    second = self.gh.posted[-1]
    self.assertEqual(second["event"], "APPROVE")
    self.assertTrue(self.gh.dismissed)
    stdins = "\n".join(c["stdin"] for c in self.fake_calls()[1:])
    self.assertIn("증분 리뷰", stdins); self.assertIn('"f1"', stdins)
    self.assertNotIn(LINE_ONLY_IN_C1, stdins)                        # first-commit diff not re-sent
def test_dry_run_posts_nothing(self): ...    # --dry-run --base/--head -> exit 0, review.json written, no POST
def test_llm_down_exit_1(self): ...          # scenario "crash" -> COMMENT "AI 리뷰 실패" posted, exit 1
```

- [ ] **Step 4: Run** `python3 -m unittest tests.test_scope tests.test_integration -v` — Expected: FAIL.
- [ ] **Step 5: Implement `review/scope.py`, `review/cli.py`, `review/__main__.py`.** Extend `fake_claude.py`
  with scenarios `blocking`, `resolved`.
- [ ] **Step 6: Run** `python3 -m unittest discover -s tests -v` — Expected: all OK.
- [ ] **Step 7: Commit** `feat: add review scope and CLI entry point`
