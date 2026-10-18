import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD = os.path.join(KIT, "review", "hooks", "guard.py")
_spec = importlib.util.spec_from_file_location("guard_under_test", GUARD)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)
check_path, check_glob_pattern = guard.check_path, guard.check_glob_pattern
check_bash, check_budget, decide = guard.check_bash, guard.check_budget, guard.decide
BUDGET_REASON = guard.BUDGET_REASON


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.realpath(os.path.join(self.tmp, "repo"))
        self.state = os.path.join(self.tmp, "state")
        os.makedirs(os.path.join(self.root, "src"))
        os.makedirs(self.state)
        os.symlink("/etc/hosts", os.path.join(self.root, "link"))

    def env(self, budget=6):
        return {"AI_REVIEW_REPO_ROOT": self.root, "AI_REVIEW_STATE_DIR": self.state,
                "AI_REVIEW_EXPLORE_BUDGET": str(budget), "PATH": os.environ.get("PATH", "")}


class PathTest(TmpCase):
    def test_inside_allowed(self):
        self.assertIsNone(check_path(os.path.join(self.root, "src/a.cu"), self.root))
        self.assertIsNone(check_path("src/a.cu", self.root))
        self.assertIsNone(check_path(None, self.root))

    def test_outside_denied(self):
        for p in ["/etc/hosts", os.path.join(self.root, "../x"), "~/.ssh/id_rsa",
                  os.path.join(self.root, "link")]:
            self.assertIn("outside repo", check_path(p, self.root))

    def test_glob_pattern(self):
        self.assertIsNone(check_glob_pattern("src/**/*.cu"))
        self.assertIsNotNone(check_glob_pattern("/etc/*"))
        self.assertIsNotNone(check_glob_pattern("../**"))
        self.assertIsNotNone(check_glob_pattern("~/x"))


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
                  "git log 'unterminated", "", "git"]:
            self.assertIsNotNone(check_bash(c), c)


class BudgetTest(TmpCase):
    def test_budget_exhaustion(self):
        reasons = [check_budget("s1", self.state, 2) for _ in range(3)]
        self.assertEqual(reasons[:2], [None, None])
        self.assertEqual(reasons[2], BUDGET_REASON)
        self.assertIsNone(check_budget("s2", self.state, 2))


class DecideTest(TmpCase):
    def test_structured_output_not_counted(self):
        self.assertIsNone(decide({"session_id": "s", "tool_name": "StructuredOutput",
                                  "tool_input": {}}, self.env(budget=0)))

    def test_read_outside_denied_before_budget(self):
        r = decide({"session_id": "s", "tool_name": "Read",
                    "tool_input": {"file_path": "/etc/hosts"}}, self.env(budget=6))
        self.assertIn("outside repo", r)
        self.assertFalse(os.path.exists(os.path.join(self.state, "budget-s.json")))

    def test_dispatch_grep_glob_bash_and_budget(self):
        env = self.env(budget=1)
        grep = {"session_id": "s", "tool_name": "Grep", "tool_input": {"pattern": "x", "path": "src"}}
        self.assertIsNone(decide(grep, env))
        self.assertEqual(decide(grep, env), BUDGET_REASON)
        glob = {"session_id": "g", "tool_name": "Glob", "tool_input": {"pattern": "/etc/*"}}
        self.assertIsNotNone(decide(glob, env))
        bash = {"session_id": "b", "tool_name": "Bash", "tool_input": {"command": "git log -1"}}
        self.assertIsNone(decide(bash, env))


def run_guard_subprocess(event, env, raw=None):
    data = raw if raw is not None else json.dumps(event)
    p = subprocess.run(["python3", "-I", GUARD], input=data, env=env, cwd=KIT,
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return p.stdout


class MainTest(TmpCase):
    def test_main_deny_json_and_audit(self):
        out = run_guard_subprocess({"session_id": "s", "tool_name": "Bash",
                                    "tool_input": {"command": "rm -rf /"}}, self.env())
        d = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(d["permissionDecision"], "deny")
        self.assertEqual(d["hookEventName"], "PreToolUse")
        with open(os.path.join(self.state, "guard-audit.jsonl")) as f:
            rec = json.loads(f.readline())
        self.assertEqual(rec["decision"], "deny")
        self.assertEqual(rec["tool"], "Bash")

    def test_main_allow_prints_nothing(self):
        out = run_guard_subprocess({"session_id": "s", "tool_name": "Read", "tool_input": {
            "file_path": os.path.join(self.root, "src/a.cu")}}, self.env())
        self.assertEqual(out, "")

    def test_main_bad_stdin_denies(self):
        out = run_guard_subprocess(None, self.env(), raw="not json")
        d = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(d["permissionDecision"], "deny")
        self.assertTrue(d["permissionDecisionReason"].startswith("guard error"))


if __name__ == "__main__":
    unittest.main()
