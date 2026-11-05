import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD = os.path.join(KIT, "review", "hooks", "guard.py")
_spec = importlib.util.spec_from_file_location("guard_under_test", GUARD)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)
check_path, check_glob_pattern = guard.check_path, guard.check_glob_pattern
check_bash, check_budget, decide = guard.check_bash, guard.check_budget, guard.decide
BUDGET_REASON, STOP_REASON, STOP_FILE = guard.BUDGET_REASON, guard.STOP_REASON, guard.STOP_FILE
TIME_STOP_REASON, TIME_STOP_FILE = guard.TIME_STOP_REASON, guard.TIME_STOP_FILE
DENY_STOP_REASON, DENY_STOP_FILE = guard.DENY_STOP_REASON, guard.DENY_STOP_FILE


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.realpath(os.path.join(self.tmp, "repo"))
        self.state = os.path.join(self.tmp, "state")
        os.makedirs(os.path.join(self.root, "src"))
        os.makedirs(self.state)
        os.symlink("/etc/hosts", os.path.join(self.root, "link"))

    def env(self, budget=6, deny_repeat=None, deny_total=None):
        env = {"AI_REVIEW_REPO_ROOT": self.root, "AI_REVIEW_STATE_DIR": self.state,
               "AI_REVIEW_EXPLORE_BUDGET": str(budget), "PATH": os.environ.get("PATH", "")}
        if deny_repeat is not None:
            env["AI_REVIEW_DENY_REPEAT_LIMIT"] = str(deny_repeat)
        if deny_total is not None:
            env["AI_REVIEW_DENY_TOTAL_LIMIT"] = str(deny_total)
        return env


class PathTest(TmpCase):
    def test_inside_allowed(self):
        self.assertIsNone(check_path(os.path.join(self.root, "src/a.cu"), self.root))
        self.assertIsNone(check_path("src/a.cu", self.root))
        self.assertIsNone(check_path(None, self.root))

    def test_outside_denied(self):
        for p in ["/etc/hosts", os.path.join(self.root, "../x"), "~/.ssh/id_rsa",
                  os.path.join(self.root, "link")]:
            self.assertIn("저장소 밖", check_path(p, self.root))

    def test_glob_pattern(self):
        self.assertIsNone(check_glob_pattern("src/**/*.cu"))
        self.assertIsNotNone(check_glob_pattern("/etc/*"))
        self.assertIsNotNone(check_glob_pattern("../**"))
        self.assertIsNotNone(check_glob_pattern("~/x"))
        self.assertIsNotNone(check_glob_pattern("{..,x}/**"))


class BashTest(unittest.TestCase):
    def test_allowed(self):
        for c in ["git diff HEAD^ -- src/a.cu", "git diff --stat -U3 a..b", "git log -n 5 --format=%h -- src",
                  "git grep -n -e foo -- src", "git grep -C3 foo", "git diff --cached", "git log -3 --oneline",
                  "git show abc123:src/a.cu", "git grep -n launch_kernel", "git blame -L 1,20 a.py"]:
            self.assertIsNone(check_bash(c), c)

    def test_denied(self):
        for c in ["ls", "cat /etc/hosts", "git push", "git -C /tmp log", "git -c core.pager=sh log",
                  "git diff --no-index /etc/hosts a", "git log --output=/tmp/x",
                  "git diff --ext-diff", "git grep -O vim x", "git grep --open-files-in-pager x",
                  "git log; rm -rf .", "git log | sh", "git log $(id)", "git log `id`",
                  "git log > out", "git log\nrm x", "git --git-dir=/x log", "git log --exec-path=/x",
                  "git log 'unterminated", "", "git",
                  "git grep -nO'touch /tmp/x' hello", "git grep --open-files-in='touch /tmp/x' hello",
                  "git grep --open=x hello", "git diff --ext=x", "git log --out=/tmp/x",
                  "git log --{output=/tmp/x,oneline}", "git log *", "git log HEAD~1",
                  "git diff /etc/hostname /dev/null", "git diff ~/.ssh/id_rsa /dev/null",
                  "git blame --contents /etc/hostname a.txt", "git blame -S /etc/hostname a",
                  "git blame --ignore-revs-file=/etc/hostname a", "git grep -f /etc/hostname",
                  "git ls-files -X /etc/hostname", "git ls-files --exclude-from=/etc/hostname",
                  "git show abc:../x", "git diff a ../b", "git log -nw", "git log --out\\put=x"]:
            self.assertIsNotNone(check_bash(c), c)


class BudgetTest(TmpCase):
    def test_budget_exhaustion(self):
        reasons = [check_budget("s1", self.state, 2) for _ in range(3)]
        self.assertEqual(reasons[:2], [None, None])
        self.assertEqual(reasons[2], BUDGET_REASON)
        self.assertIsNone(check_budget("s2", self.state, 2))

    def test_second_overrun_stops(self):
        stop = os.path.join(self.state, STOP_FILE)
        reasons = [check_budget("s1", self.state, 1) for _ in range(2)]
        self.assertEqual(reasons, [None, BUDGET_REASON])
        self.assertFalse(os.path.exists(stop))
        self.assertEqual([check_budget("s1", self.state, 1) for _ in range(2)],
                         [STOP_REASON, STOP_REASON])
        self.assertTrue(os.path.exists(stop))


class DecideTest(TmpCase):
    def test_structured_output_not_counted(self):
        self.assertIsNone(decide({"session_id": "s", "tool_name": "StructuredOutput",
                                  "tool_input": {}}, self.env(budget=0)))

    def test_read_outside_denied_before_budget(self):
        r = decide({"session_id": "s", "tool_name": "Read",
                    "tool_input": {"file_path": "/etc/hosts"}}, self.env(budget=6))
        self.assertIn("저장소 밖", r)
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


def _bash(cmd, sid="s"):
    return {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": cmd}}


class DenyLimitTest(TmpCase):
    CAT = _bash("cat a.c")

    def marker(self):
        return os.path.exists(os.path.join(self.state, DENY_STOP_FILE))

    def test_two_identical_denials_are_not_a_stop_third_is(self):
        env = self.env()
        for _ in range(2):
            self.assertEqual(decide(self.CAT, env), check_bash("cat a.c"))
        self.assertFalse(self.marker())
        self.assertEqual(decide(self.CAT, env), DENY_STOP_REASON)
        self.assertEqual(decide(self.CAT, env), DENY_STOP_REASON)
        self.assertTrue(self.marker())

    def test_whitespace_variants_are_identical(self):
        env = self.env()
        for c in ["cat a.c", "cat   a.c"]:
            self.assertNotEqual(decide(_bash(c), env), DENY_STOP_REASON)
        self.assertEqual(decide(_bash(" cat a.c\t"), env), DENY_STOP_REASON)

    def test_different_inputs_and_tools_are_different_calls(self):
        env = self.env()
        read = {"session_id": "s", "tool_name": "Read", "tool_input": {"file_path": "/etc/passwd"}}
        for call in [self.CAT, read, _bash("cat b.c")] * 2:
            self.assertNotEqual(decide(call, env), DENY_STOP_REASON)
        self.assertFalse(self.marker())

    def test_twenty_different_denials_do_not_stop_the_21st_does(self):
        env = self.env()
        for i in range(20):
            self.assertNotEqual(decide(_bash("cat f%d.c" % i), env), DENY_STOP_REASON, i)
        self.assertFalse(self.marker())
        self.assertEqual(decide(_bash("cat f20.c"), env), DENY_STOP_REASON)
        self.assertTrue(self.marker())

    def test_limits_from_env(self):
        env = self.env(deny_repeat=2)
        self.assertNotEqual(decide(self.CAT, env), DENY_STOP_REASON)
        self.assertEqual(decide(self.CAT, env), DENY_STOP_REASON)
        env = self.env(deny_total=1)
        decide(_bash("cat a", "u"), env)
        self.assertEqual(decide(_bash("cat b", "u"), env), DENY_STOP_REASON)

    def test_sessions_do_not_share_counters(self):
        env = self.env()
        for _ in range(2):
            decide(self.CAT, env)
        self.assertNotEqual(decide(dict(self.CAT, session_id="t"), env), DENY_STOP_REASON)
        self.assertEqual(decide(self.CAT, env), DENY_STOP_REASON)

    def test_answer_tool_exempt_and_allowed_calls_not_counted(self):
        env = self.env(deny_total=0)
        answer = {"session_id": "s", "tool_name": "StructuredOutput", "tool_input": {}}
        ok = {"session_id": "s", "tool_name": "Read", "tool_input": {"file_path": "src/a.c"}}
        self.assertIsNone(decide(answer, env))
        self.assertIsNone(decide(ok, env))
        self.assertFalse(self.marker())
        self.assertEqual(decide(self.CAT, env), DENY_STOP_REASON)
        self.assertIsNone(decide(answer, env))

    def test_denials_do_not_charge_the_budget(self):
        env = self.env(budget=3)
        for i in range(10):
            decide(_bash("cat f%d" % i), env)
        self.assertFalse(os.path.exists(os.path.join(self.state, "budget-s.json")))
        ok = {"session_id": "s", "tool_name": "Read", "tool_input": {"file_path": "src/a.c"}}
        self.assertEqual([decide(ok, env) for _ in range(3)], [None] * 3)
        self.assertEqual(decide(ok, env), BUDGET_REASON)

    def test_counter_file_stays_small(self):
        env = self.env()
        for i in range(20):
            decide(_bash("cat " + "x" * 500 + str(i)), env)
        self.assertLess(os.path.getsize(os.path.join(self.state, "denied-s.json")), 2000)

    def test_stop_output_and_audit(self):
        env = dict(self.env(deny_total=0), AI_REVIEW_STATE_DIR=self.state)
        p = subprocess.run([sys.executable, "-I", GUARD], input=json.dumps(self.CAT),
                           capture_output=True, text=True, env=env)
        out = json.loads(p.stdout)
        self.assertIs(out["continue"], False)
        self.assertEqual(out["stopReason"], DENY_STOP_REASON)
        with open(os.path.join(self.state, "guard-audit.jsonl")) as f:
            self.assertEqual(json.loads(f.readline())["decision"], "stop")


class DenialGuidanceTest(TmpCase):
    def calls(self):
        yield _bash("cat x"), ["Read"]
        yield _bash("cat a | head"), ["Read"]
        yield _bash("ls"), ["Glob"]
        yield _bash("find . -name x"), ["Glob"]
        yield _bash("rg foo"), ["Grep"]
        yield _bash("make"), ["Read", "Grep", "Glob"]
        yield _bash("python3 x.py"), ["Read", "Grep", "Glob"]
        yield _bash("git log | head"), ["파이프"]
        yield _bash("git push"), ["diff", "log", "ls-files"]
        yield _bash("git log --badflag"), ["--badflag"]
        yield {"session_id": "s", "tool_name": "Read",
               "tool_input": {"file_path": "/etc/passwd"}}, ["Read", "저장소 밖"]
        yield {"session_id": "s", "tool_name": "Glob", "tool_input": {"pattern": "../x"}}, ["Glob", "저장소 밖"]

    def test_hints(self):
        for call, words in self.calls():
            r = decide(call, self.env())
            self.assertIsNotNone(r, call)
            self.assertNotEqual(r, DENY_STOP_REASON)
            for w in words:
                self.assertIn(w, r, call)
            self.assertIn("예산", r, call)
            self.assertIn("차감되지 않았다", r, call)
            self.assertIn("다시 시도하지 마라", r, call)
            self.assertIn("계속", r, call)
            self.assertNotIn("결론", r, call)
            self.assertNotIn("최종", r, call)

    def test_outside_texts_do_not_suggest_reaching_out(self):
        for r in [check_path("/etc/passwd", self.root), check_glob_pattern("../x"),
                  check_bash("git show a:../x")]:
            self.assertIn("저장소 밖", r)
            self.assertNotIn("결론", r)

    def test_all_samples_still_denied(self):
        for call, _ in self.calls():
            self.assertIsNotNone(decide(call, self.env(budget=100)), call)
        self.assertIsNotNone(check_path("/etc/passwd", self.root))


class TimeStopTest(TmpCase):
    READ = {"session_id": "s", "tool_name": "Read", "tool_input": {"file_path": "src/a.cu"}}

    def at(self, stop_at):
        return dict(self.env(), AI_REVIEW_STOP_AT=stop_at)

    def marker(self):
        return os.path.exists(os.path.join(self.state, TIME_STOP_FILE))

    def test_no_stop_before_the_time(self):
        self.assertIsNone(decide(self.READ, self.at(str(time.time() + 60))))
        self.assertFalse(self.marker())

    def test_stops_at_or_after_the_time(self):
        self.assertEqual(decide(self.READ, self.at(str(time.time() - 1))), TIME_STOP_REASON)
        self.assertTrue(self.marker())
        self.assertFalse(os.path.exists(os.path.join(self.state, STOP_FILE)))

    def test_missing_or_empty_means_no_time_stop(self):
        self.assertIsNone(decide(self.READ, self.env()))
        self.assertIsNone(decide(self.READ, self.at("")))
        self.assertFalse(self.marker())

    def test_applies_before_rule_denials(self):
        bad = {"session_id": "s", "tool_name": "Read", "tool_input": {"file_path": "/etc/hosts"}}
        self.assertEqual(decide(bad, self.at("1")), TIME_STOP_REASON)
        other = {"session_id": "s", "tool_name": "Task", "tool_input": {}}
        self.assertEqual(decide(other, self.at("1")), TIME_STOP_REASON)

    def test_final_answer_is_never_stopped(self):
        ans = {"session_id": "s", "tool_name": "StructuredOutput", "tool_input": {}}
        self.assertIsNone(decide(ans, self.at("1")))
        self.assertFalse(self.marker())

    def test_main_stops_session_and_audits(self):
        d = json.loads(run_guard_subprocess(self.READ, self.at("1")))
        self.assertEqual((d["continue"], d["stopReason"]), (False, TIME_STOP_REASON))
        self.assertEqual(d["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("탐색 시간 소진", d["hookSpecificOutput"]["permissionDecisionReason"])
        with open(os.path.join(self.state, "guard-audit.jsonl")) as f:
            self.assertEqual(json.loads(f.readline())["decision"], "stop")


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

    def test_main_first_overrun_denies_second_stops(self):
        ev = {"session_id": "s", "tool_name": "Read",
              "tool_input": {"file_path": os.path.join(self.root, "src/a.cu")}}
        outs = [run_guard_subprocess(ev, self.env(budget=1)) for _ in range(3)]
        self.assertEqual(outs[0], "")
        first, second = json.loads(outs[1]), json.loads(outs[2])
        self.assertNotIn("continue", first)
        self.assertEqual(first["hookSpecificOutput"]["permissionDecisionReason"], BUDGET_REASON)
        self.assertEqual((second["continue"], second["stopReason"]), (False, STOP_REASON))
        d = second["hookSpecificOutput"]
        self.assertEqual((d["permissionDecision"], d["permissionDecisionReason"]),
                         ("deny", BUDGET_REASON))
        with open(os.path.join(self.state, "guard-audit.jsonl")) as f:
            self.assertEqual([json.loads(x)["decision"] for x in f], ["allow", "deny", "stop"])

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
