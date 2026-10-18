# Plan Part 2 — Guard Hook and Permission Settings

Read `00-overview.md` first. Spec sections 4.2 and 4.3.

### Task 3: PreToolUse guard and `config/settings.json`

**Files:**
- Create: `review/hooks/guard.py` (self-contained: stdlib only, imports nothing from `review`),
  `config/settings.json`
- Test: `tests/test_guard.py`, `tests/test_settings.py`

**Interfaces:**
- Consumes: env `AI_REVIEW_REPO_ROOT`, `AI_REVIEW_STATE_DIR`, `AI_REVIEW_EXPLORE_BUDGET` (set by Task 6).
- Produces:
  - `check_path(path: str | None, root: str) -> str | None` — deny reason or None.
  - `check_glob_pattern(pattern: str) -> str | None`
  - `check_bash(command: str) -> str | None`
  - `check_budget(session_id: str, state_dir: str, budget: int) -> str | None` — increments the counter.
  - `decide(event: dict, env: dict) -> str | None` — dispatch by `event["tool_name"]`.
  - `main() -> int` — reads hook JSON on stdin; on deny prints
    `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
    "permissionDecisionReason": <reason>}}` and returns 0; on allow prints nothing, returns 0.
    Every decision appends `{"ts","session_id","tool","input","decision","reason"}` to
    `<state_dir>/guard-audit.jsonl`. Any internal exception → deny with reason `"guard error: <type>"`.
  - Constants: `EXPLORE_TOOLS = {"Read", "Grep", "Glob", "Bash"}`,
    `GIT_SUBCOMMANDS = {"diff", "log", "show", "blame", "grep", "ls-files"}`,
    `BUDGET_REASON = "탐색 예산 소진. 추가 도구 호출 없이 지금 최종 JSON 결과를 출력하라."`

Tools outside `EXPLORE_TOOLS` (including the structured-output tool) are neither checked nor counted.

- [ ] **Step 1: Write `tests/test_guard.py`** (temp repo dir as root; a symlink `link -> /etc/hosts` inside it):

```python
class PathTest(unittest.TestCase):
    def test_inside_allowed(self):
        self.assertIsNone(check_path(os.path.join(ROOT, "src/a.cu"), ROOT))
        self.assertIsNone(check_path("src/a.cu", ROOT))          # relative -> joined to root
        self.assertIsNone(check_path(None, ROOT))                # Grep/Glob default path
    def test_outside_denied(self):
        for p in ["/etc/hosts", os.path.join(ROOT, "../x"), "~/.ssh/id_rsa",
                  os.path.join(ROOT, "link")]:
            self.assertIn("outside repo", check_path(p, ROOT))
    def test_glob_pattern(self):
        self.assertIsNone(check_glob_pattern("src/**/*.cu"))
        self.assertIsNotNone(check_glob_pattern("/etc/*"))
        self.assertIsNotNone(check_glob_pattern("../**"))

class BashTest(unittest.TestCase):
    def test_allowed(self):
        for c in ["git diff HEAD~1 -- src/a.cu", "git log -3 --oneline",
                  "git show abc123:src/a.cu", "git grep -n launch_kernel", "git blame -L 1,20 a.py"]:
            self.assertIsNone(check_bash(c), c)
    def test_denied(self):
        for c in ["ls", "cat /etc/hosts", "git push", "git -C /tmp log", "git -c core.pager=sh log",
                  "git diff --no-index /etc/hosts a", "git log --output=/tmp/x",
                  "git diff --ext-diff", "git grep -O vim x", "git grep --open-files-in-pager x",
                  "git log; rm -rf .", "git log | sh", "git log $(id)", "git log `id`",
                  "git log > out", "git log\nrm x", "git --git-dir=/x log", "git log --exec-path=/x",
                  "git log 'unterminated"]:
            self.assertIsNotNone(check_bash(c), c)

class BudgetTest(unittest.TestCase):
    def test_budget_exhaustion(self):
        reasons = [check_budget("s1", self.state, 2) for _ in range(3)]
        self.assertEqual(reasons[:2], [None, None])
        self.assertEqual(reasons[2], BUDGET_REASON)
        self.assertIsNone(check_budget("s2", self.state, 2))     # per session

class DecideTest(unittest.TestCase):
    def test_structured_output_not_counted(self):
        env = self.env(budget=0)
        self.assertIsNone(decide({"session_id": "s", "tool_name": "StructuredOutput",
                                  "tool_input": {}}, env))
    def test_read_outside_denied_before_budget(self):
        r = decide({"session_id": "s", "tool_name": "Read",
                    "tool_input": {"file_path": "/etc/hosts"}}, self.env(budget=6))
        self.assertIn("outside repo", r)

class MainTest(unittest.TestCase):
    def test_main_deny_json_and_audit(self):
        out = run_guard_subprocess({"session_id": "s", "tool_name": "Bash",
                                    "tool_input": {"command": "rm -rf /"}}, self.env())
        d = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(d["permissionDecision"], "deny")
        self.assertTrue(os.path.exists(os.path.join(self.state, "guard-audit.jsonl")))
    def test_main_allow_prints_nothing(self): ...   # Read inside root -> stdout == ""
    def test_main_bad_stdin_denies(self): ...       # stdin "not json" -> deny, reason starts "guard error"
```

`run_guard_subprocess` runs `python3 -I review/hooks/guard.py` with the JSON on stdin and the env.

- [ ] **Step 2: Write `tests/test_settings.py`**

```python
def test_settings_rules(self):
    s = json.load(open("config/settings.json"))
    allow, deny = s["permissions"]["allow"], s["permissions"]["deny"]
    for r in ["Read(./**)", "Grep(./**)", "Glob(./**)", "Bash(git diff:*)", "Bash(git log:*)",
              "Bash(git show:*)", "Bash(git blame:*)", "Bash(git grep:*)", "Bash(git ls-files:*)"]:
        self.assertIn(r, allow)
    for r in ["Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch", "Bash(git push:*)",
              "Bash(git commit:*)", "Bash(git checkout:*)", "Bash(git reset:*)", "Bash(git config:*)"]:
        self.assertIn(r, deny)
    self.assertEqual(s["permissions"]["defaultMode"], "dontAsk")
    hook = s["hooks"]["PreToolUse"][0]
    self.assertEqual(hook["matcher"], "*")
    self.assertIn("review/hooks/guard.py", hook["hooks"][0]["command"])
```

- [ ] **Step 3: Run** `python3 -m unittest tests.test_guard tests.test_settings -v` — Expected: FAIL.
- [ ] **Step 4: Implement `guard.py`.** `check_path`: expand `~`, join relative paths to root, `os.path.realpath`
  both, deny unless `commonpath == root`; reason `"path outside repo: <p>"`. `check_bash`: deny if any of
  `;|&><$\`()` or `\n`/`\r` present; `shlex.split` (ValueError → deny); require `tokens[0] == "git"` and
  `tokens[1] in GIT_SUBCOMMANDS` (so no global options); deny any token equal to `-c`, `-C`, `-O` or starting
  with `--output`, `--git-dir`, `--work-tree`, `--no-index`, `--ext-diff`, `--exec-path`,
  `--open-files-in-pager`, `-O`. `check_budget`: counter file `budget-<session_id>.json` guarded by
  `fcntl.flock` (parallel tool calls). Order in `decide`: path/bash checks first, then budget.
- [ ] **Step 5: Write `config/settings.json`**: `permissions.allow`/`deny` exactly as the test lists,
  `"defaultMode": "dontAsk"`, and hook `{"type": "command", "timeout": 10,
  "command": "python3 -I \"$AI_REVIEW_KIT/review/hooks/guard.py\""}` under `PreToolUse` with matcher `"*"`.
- [ ] **Step 6: Run** `python3 -m unittest discover -s tests -v` — Expected: all OK.
- [ ] **Step 7: Commit** `feat: add PreToolUse guard and permission settings`
