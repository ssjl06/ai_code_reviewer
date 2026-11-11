import os
import tempfile
import unittest

from review import rules, rules_edit

DOC = {"applies": ["**/*.cu"], "title": "CUDA 스트림 동기화",
       "rules": [{"id": "R-aaaaaa", "text": "규칙 하나", "reason": "이유 하나", "source": "PR #1"}]}


def change(**kw):
    c = {"op": "add", "path": "cuda/stream-sync.md", "applies": ["**/*.cu"], "rule": "규칙 하나",
         "reason": "이유 하나", "target": None}
    c.update(kw)
    return c


class RulesEditTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = tmp.name

    def file(self, rel):
        return os.path.join(self.repo, rules.RULES_DIR, rel)

    def read(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def put(self, rel, text):
        p = self.file(rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)

    def test_add_twice_unchanged_and_update_remove(self):
        r = rules_edit.apply_change(self.repo, change(), "PR #1")
        self.assertEqual(r["status"], "added")
        rid = r["rule_id"]
        self.assertEqual(rid, rules.rule_id("cuda/stream-sync.md", "규칙 하나"))
        self.assertEqual(rules_edit.apply_change(self.repo, change(), "PR #1")["status"], "unchanged")
        u = rules_edit.apply_change(self.repo, change(op="update", target=rid, rule="새 규칙",
                                                 reason="새 이유"), "PR #2")
        self.assertEqual(u["status"], "updated")
        doc = rules.load_docs(self.repo)["cuda/stream-sync.md"]
        self.assertEqual([(x["text"], x["reason"], x["source"]) for x in doc["rules"]],
                         [("새 규칙", "새 이유", "PR #2")])
        self.assertEqual(u["rule_id"], doc["rules"][0]["id"])
        self.assertEqual(rules_edit.apply_change(self.repo, change(op="remove", target="R-000000"),
                                            "s")["status"], "invalid")
        rm = rules_edit.apply_change(self.repo, change(op="remove", target=u["rule_id"]), "s")
        self.assertEqual(rm["status"], "removed")
        self.assertFalse(os.path.exists(self.file("cuda/stream-sync.md")))

    def test_invalid_changes(self):
        for c in (change(path="../x.md"), change(rule="a\nb"), change(reason=""),
                  change(applies=[]), change(op="update", target=None)):
            r = rules_edit.apply_change(self.repo, c, "s")
            self.assertEqual(r["status"], "invalid", c)
            self.assertTrue(r["reason"])
        self.assertEqual(rules_edit.apply_change(self.repo, change(), "a\nb")["status"], "invalid")

    def test_overflow_and_free_path(self):
        for n in range(32):
            self.assertEqual(rules_edit.apply_change(self.repo, change(rule="r%d" % n), "s")["status"],
                             "added")
        before = self.read(self.file("cuda/stream-sync.md"))
        self.assertEqual(before.count("\n"), 100)
        r = rules_edit.apply_change(self.repo, change(rule="one more"), "s")
        self.assertEqual(r["status"], "overflow")
        self.assertEqual(self.read(self.file("cuda/stream-sync.md")), before)
        self.assertEqual(rules_edit.free_path(self.repo, "cuda/stream-sync.md"), "cuda/stream-sync-2.md")
        self.assertEqual(rules_edit.free_path(self.repo, "cuda/other.md"), "cuda/other.md")
        self.put("cuda/stream-sync-2.md", before)
        self.assertEqual(rules_edit.free_path(self.repo, "cuda/stream-sync.md"), "cuda/stream-sync-3.md")

    def test_shrink_over_limit_file(self):
        doc = {"applies": ["**/*.cu"], "title": "t", "rules": [
            {"id": rules.rule_id("cuda/big.md", "r%d" % n), "text": "r%d" % n, "reason": "x",
             "source": "s"} for n in range(40)]}
        self.put("cuda/big.md", rules.render_doc(doc))
        self.assertGreater(self.read(self.file("cuda/big.md")).count("\n"), 100)
        r = rules_edit.apply_change(
            self.repo, change(op="remove", path="cuda/big.md", target=doc["rules"][0]["id"]), "s")
        self.assertEqual(r["status"], "removed")
        self.assertEqual(rules_edit.apply_change(self.repo, change(path="cuda/big.md", rule="n"),
                                                 "s")["status"], "overflow")

    def test_index_symlinks_not_followed(self):
        rules_edit.apply_change(self.repo, change(), "s")
        outside = os.path.join(self.repo, "outside")
        os.makedirs(outside)
        target = os.path.join(outside, "t.md")
        with open(target, "w") as f:
            f.write("keep")
        os.symlink(target, self.file("INDEX.md"))
        os.symlink(target, self.file("cuda/INDEX.md"))
        rules_edit.write_indexes(self.repo)
        self.assertEqual(self.read(target), "keep")
        # a symlinked category directory: its target directory stays untouched
        victim = os.path.join(self.repo, "victim")
        os.makedirs(victim)
        with open(os.path.join(victim, "INDEX.md"), "w") as f:
            f.write("keep")
        os.symlink(victim, self.file("python"))
        rules_edit.write_indexes(self.repo)
        self.assertEqual(self.read(os.path.join(victim, "INDEX.md")), "keep")

    def test_write_indexes(self):
        rules_edit.apply_change(self.repo, change(), "s")
        rules_edit.apply_change(self.repo, change(path="python/a.md", applies=["**/*.py"]), "s")
        written = rules_edit.write_indexes(self.repo)
        self.assertEqual(sorted(written), sorted([
            rules.RULES_DIR + "/INDEX.md", rules.RULES_DIR + "/cuda/INDEX.md",
            rules.RULES_DIR + "/python/INDEX.md"]))
        root = self.read(self.file("INDEX.md"))
        self.assertIn("cuda/", root)
        self.assertIn("python/", root)
        self.assertNotIn("shell/", root)
        self.assertIn("cuda/stream-sync.md", self.read(self.file("cuda/INDEX.md")))
        rules_edit.apply_change(self.repo, change(op="remove", target=rules.rule_id(
            "cuda/stream-sync.md", "규칙 하나")), "s")
        rules_edit.write_indexes(self.repo)
        self.assertFalse(os.path.exists(self.file("cuda/INDEX.md")))
        self.assertNotIn("cuda/", self.read(self.file("INDEX.md")))
        # the generated indexes are not loaded as guideline files
        self.assertEqual(list(rules.load_docs(self.repo)), ["python/a.md"])



if __name__ == "__main__":
    unittest.main()
