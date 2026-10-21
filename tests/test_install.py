"""install.sh: copies the kit into <target>/.github/ai-review and the workflow template,
and the installed copy runs from there against the target repository."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

from tests.fakes import FAKE

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL = os.path.join(KIT, "install.sh")
TEMPLATE = os.path.join(KIT, "templates", "ai-review.yml")
BUGGY = "int k(int *p) {\n  return p[1];\n}\n"


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                          text=True).stdout.strip()


def tree(root):
    out = set()
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x != ".git"]
        for f in files:
            out.add(os.path.relpath(os.path.join(d, f), root))
    return out


def kit_files(sub):
    return {os.path.join(sub, os.path.relpath(os.path.join(d, f), os.path.join(KIT, sub)))
            for d, dirs, files in os.walk(os.path.join(KIT, sub))
            if "__pycache__" not in d for f in files if not f.endswith(".pyc")}


class InstallBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = os.path.realpath(tmp.name)
        self.T = os.path.join(self.tmp, "target")
        os.makedirs(self.T)
        git(self.T, "init", "-q")
        git(self.T, "config", "user.email", "t@example.com")
        git(self.T, "config", "user.name", "t")
        self.write("README.md", "r\n")
        self.write(".github/CODEOWNERS", "* @o\n")
        git(self.T, "add", "-A")
        git(self.T, "commit", "-q", "-m", "base")
        self.BASE = git(self.T, "rev-parse", "HEAD")
        self.kit_dst = os.path.join(self.T, ".github", "ai-review")
        self.wf = os.path.join(self.T, ".github", "workflows", "ai-review.yml")

    def write(self, rel, content):
        path = os.path.join(self.T, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def install(self, *args):
        p = subprocess.run(["bash", INSTALL, *args, self.T], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout


class InstallTest(InstallBase):
    def test_layout_update_and_workflow_kept(self):
        before = tree(self.T)
        out = self.install()
        self.assertIn("AI_REVIEW_BOT_PAT", out)
        self.assertIn("git commit", out)
        kit = tree(self.kit_dst)
        expected = kit_files("review") | kit_files("agent") | kit_files("config")
        self.assertTrue(expected <= kit, expected - kit)
        self.assertEqual(kit - expected, {"README.md", "VERSION", ".gitignore"})
        self.assertFalse(any("__pycache__" in p or p.endswith(".pyc") for p in kit))
        self.assertFalse(any(p.startswith("tests") for p in kit))
        with open(os.path.join(self.kit_dst, "VERSION"), encoding="utf-8") as f:
            self.assertTrue(f.read().startswith(git(KIT, "rev-parse", "HEAD")))
        with open(self.wf, encoding="utf-8") as f, open(TEMPLATE, encoding="utf-8") as t:
            self.assertEqual(f.read(), t.read())
        # nothing else in the target changed
        added = tree(self.T) - before
        self.assertEqual(added, {os.path.join(".github/ai-review", p) for p in kit}
                         | {".github/workflows/ai-review.yml"})
        self.assertFalse([e for e in os.listdir(os.path.join(self.T, ".github"))
                          if e.startswith(".ai-review.tmp")])

        # second run replaces the kit; the edited workflow is kept without the flag
        self.write(".github/ai-review/stale.py", "x\n")
        self.write(".github/ai-review/review/cli.py", "broken\n")
        self.write(".github/workflows/ai-review.yml", "custom\n")
        out = self.install()
        self.assertIn("kept existing", out)
        self.assertFalse(os.path.exists(os.path.join(self.kit_dst, "stale.py")))
        with open(os.path.join(self.kit_dst, "review", "cli.py"), encoding="utf-8") as f, \
                open(os.path.join(KIT, "review", "cli.py"), encoding="utf-8") as g:
            self.assertEqual(f.read(), g.read())
        with open(self.wf, encoding="utf-8") as f:
            self.assertEqual(f.read(), "custom\n")
        self.assertEqual(tree(self.kit_dst), kit)

        self.install("--force-workflow")
        with open(self.wf, encoding="utf-8") as f, open(TEMPLATE, encoding="utf-8") as t:
            self.assertEqual(f.read(), t.read())
        with open(os.path.join(self.T, ".github/CODEOWNERS"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "* @o\n")

    def test_bad_usage(self):
        for args in ([], ["--nope", self.T], [os.path.join(self.tmp, "missing")], [KIT]):
            p = subprocess.run(["bash", INSTALL, *args], capture_output=True, text=True)
            self.assertNotEqual(p.returncode, 0, args)
        self.assertFalse(os.path.exists(self.kit_dst))


WRAPPER = r'''#!/usr/bin/env python3
"""Records what the kit hands to `claude`, runs the configured guard hook, then fakes claude."""
import json, os, subprocess, sys
argv = sys.argv[1:]
settings = argv[argv.index("--settings") + 1]
agents = argv[argv.index("--agents") + 1]
with open(settings, encoding="utf-8") as f:
    hook = json.load(f)["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
root = os.environ["AI_REVIEW_REPO_ROOT"]
def guard(path):
    ev = {"session_id": "s", "tool_name": "Read", "tool_input": {"file_path": path}}
    p = subprocess.run(["sh", "-c", hook], input=json.dumps(ev), capture_output=True, text=True)
    return {"rc": p.returncode, "out": p.stdout, "err": p.stderr}
rec = {"settings": settings, "agents": json.load(open(agents, encoding="utf-8")),
       "kit": os.environ.get("AI_REVIEW_KIT"), "root": root, "hook": hook,
       "inside": guard(os.path.join(root, "src", "k.cu")), "outside": guard("/etc/hosts")}
with open(%(rec)r, "a", encoding="utf-8") as f:
    f.write(json.dumps(rec) + "\n")
os.execv(sys.executable, [sys.executable, %(fake)r, "--scenario", "blocking",
                          "--log", %(log)r] + argv)
'''


class InRepoRunTest(InstallBase):
    def test_dry_run_from_installed_kit(self):
        self.install()
        marker = "IN-REPO-AGENT-MARKER"
        with open(os.path.join(self.kit_dst, "agent", "pr-reviewer.md"), "a",
                  encoding="utf-8") as f:
            f.write("\n" + marker + "\n")
        self.write("src/k.cu", BUGGY)
        git(self.T, "add", "-A")
        git(self.T, "commit", "-q", "-m", "install kit + change")
        head = git(self.T, "rev-parse", "HEAD")
        changed = git(self.T, "diff", "--name-only", self.BASE, head).split("\n")
        self.assertIn(".github/ai-review/review/cli.py", changed)

        rec, log = os.path.join(self.tmp, "rec.jsonl"), os.path.join(self.tmp, "calls.jsonl")
        claude = os.path.join(self.tmp, "claude-wrapper")
        with open(claude, "w", encoding="utf-8") as f:
            f.write(WRAPPER % {"rec": rec, "fake": FAKE, "log": log})
        os.chmod(claude, 0o755)
        out_dir = os.path.join(self.tmp, "out")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("AI_REVIEW_", "PYTHON"))}
        env.update({"AI_REVIEW_LLM_BASE_URL": "http://llm.invalid", "AI_REVIEW_LLM_API_KEY": "k",
                    "RUNNER_TEMP": self.tmp})
        p = subprocess.run([sys.executable, "-E", "-s", "-m", "review", "--repo", self.T,
                            "--dry-run", "--base", self.BASE, "--head", head,
                            "--out-dir", out_dir, "--set", "claude_bin=" + claude],
                           cwd=self.kit_dst, env=env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

        with open(rec, encoding="utf-8") as f:
            recs = [json.loads(x) for x in f]
        self.assertEqual(len(recs), 1)  # one chunk: src/k.cu only
        r = recs[0]
        self.assertEqual(r["settings"], os.path.join(self.kit_dst, "config", "settings.json"))
        self.assertEqual(os.path.realpath(r["kit"]), self.kit_dst)
        self.assertEqual(os.path.realpath(r["root"]), self.T)
        self.assertIn(marker, json.dumps(r["agents"]))
        self.assertEqual((r["inside"]["rc"], r["inside"]["out"].strip()), (0, ""),
                         r["inside"])
        self.assertEqual(r["outside"]["rc"], 0, r["outside"])
        self.assertIn('"permissionDecision": "deny"', r["outside"]["out"])

        with open(log, encoding="utf-8") as f:
            stdin = "\n".join(json.loads(x)["stdin"] for x in f)
        self.assertIn("diff --git a/src/k.cu", stdin)
        # the installed kit is excluded; the workflow file itself is still reviewed
        self.assertIn("diff --git a/.github/workflows/ai-review.yml", stdin)
        self.assertNotIn("a/.github/ai-review/", stdin)
        self.assertNotIn("- .github/ai-review/", stdin)
        with open(os.path.join(out_dir, "review.json"), encoding="utf-8") as f:
            review = json.load(f)
        self.assertEqual(review["event"], "REQUEST_CHANGES")
        self.assertEqual([c["path"] for c in review["comments"]], ["src/k.cu"])
        self.assertNotIn(".github/ai-review/", review["body"])


if __name__ == "__main__":
    unittest.main()
