import os
import tempfile
import unittest

from review import rules

DOC = {"applies": ["**/*.cu"], "title": "CUDA 스트림 동기화",
       "rules": [{"id": "R-aaaaaa", "text": "규칙 하나", "reason": "이유 하나", "source": "PR #1"}]}


def change(**kw):
    c = {"op": "add", "path": "cuda/stream-sync.md", "applies": ["**/*.cu"], "rule": "규칙 하나",
         "reason": "이유 하나", "target": None}
    c.update(kw)
    return c


class RulesTest(unittest.TestCase):
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

    def test_rule_id_stable(self):
        a = rules.rule_id("cuda/x.md", "r")
        self.assertEqual(a, rules.rule_id("cuda/x.md", "r"))
        self.assertRegex(a, r"^R-[0-9a-f]{6}$")
        self.assertNotEqual(a, rules.rule_id("cuda/y.md", "r"))

    def test_round_trip(self):
        self.assertEqual(rules.parse_doc(rules.render_doc(DOC)), DOC)

    def test_broken_docs(self):
        self.assertIsNone(rules.parse_doc("no frontmatter"))
        self.assertIsNone(rules.parse_doc('---\napplies: nope\ntitle: t\n---\n'))
        self.assertIsNone(rules.parse_doc('---\napplies: [1]\ntitle: t\n---\n'))
        self.assertIsNone(rules.parse_doc('---\napplies: ["a"]\ntitle: t\n---\n## R-aaaaaa  x\n'))

    def test_check_path(self):
        self.assertIsNone(rules.check_path("cuda/stream-sync.md"))
        for bad in ("../x.md", "cuda/Sync.md", "other/x.md", "cuda/a/b.md", "cuda/x.txt"):
            self.assertIsInstance(rules.check_path(bad), str, bad)

    def test_add_twice_unchanged_and_update_remove(self):
        r = rules.apply_change(self.repo, change(), "PR #1")
        self.assertEqual(r["status"], "added")
        rid = r["rule_id"]
        self.assertEqual(rid, rules.rule_id("cuda/stream-sync.md", "규칙 하나"))
        self.assertEqual(rules.apply_change(self.repo, change(), "PR #1")["status"], "unchanged")
        u = rules.apply_change(self.repo, change(op="update", target=rid, rule="새 규칙",
                                                 reason="새 이유"), "PR #2")
        self.assertEqual(u["status"], "updated")
        doc = rules.load_docs(self.repo)["cuda/stream-sync.md"]
        self.assertEqual([(x["text"], x["reason"], x["source"]) for x in doc["rules"]],
                         [("새 규칙", "새 이유", "PR #2")])
        self.assertEqual(u["rule_id"], doc["rules"][0]["id"])
        self.assertEqual(rules.apply_change(self.repo, change(op="remove", target="R-000000"),
                                            "s")["status"], "invalid")
        rm = rules.apply_change(self.repo, change(op="remove", target=u["rule_id"]), "s")
        self.assertEqual(rm["status"], "removed")
        self.assertFalse(os.path.exists(self.file("cuda/stream-sync.md")))

    def test_invalid_changes(self):
        for c in (change(path="../x.md"), change(rule="a\nb"), change(reason=""),
                  change(applies=[]), change(op="update", target=None)):
            r = rules.apply_change(self.repo, c, "s")
            self.assertEqual(r["status"], "invalid", c)
            self.assertTrue(r["reason"])
        self.assertEqual(rules.apply_change(self.repo, change(), "a\nb")["status"], "invalid")

    def test_overflow_and_free_path(self):
        for n in range(32):
            self.assertEqual(rules.apply_change(self.repo, change(rule="r%d" % n), "s")["status"],
                             "added")
        before = self.read(self.file("cuda/stream-sync.md"))
        self.assertEqual(before.count("\n"), 100)
        r = rules.apply_change(self.repo, change(rule="one more"), "s")
        self.assertEqual(r["status"], "overflow")
        self.assertEqual(self.read(self.file("cuda/stream-sync.md")), before)
        self.assertEqual(rules.free_path(self.repo, "cuda/stream-sync.md"), "cuda/stream-sync-2.md")
        self.assertEqual(rules.free_path(self.repo, "cuda/other.md"), "cuda/other.md")
        self.put("cuda/stream-sync-2.md", before)
        self.assertEqual(rules.free_path(self.repo, "cuda/stream-sync.md"), "cuda/stream-sync-3.md")

    def test_load_skips_broken_and_symlink(self):
        self.put("cuda/a.md", rules.render_doc(DOC))
        self.put("cuda/INDEX.md", "x")
        self.put("cuda/broken.md", "nothing")
        os.symlink(self.file("cuda/a.md"), self.file("cuda/link.md"))
        docs = rules.load_docs(self.repo)
        self.assertEqual(list(docs), ["cuda/a.md"])

    def test_select_order_and_cap(self):
        def doc(globs, text="x" * 50):
            return {"applies": globs, "title": "t",
                    "rules": [{"id": "R-aaaaaa", "text": text, "reason": "r", "source": ""}]}
        docs = {"cpp/z.md": doc(["**/*.cu"]), "paths/p.md": doc(["src/**"]),
                "cuda/a.md": doc(["**/*.cu"]), "python/n.md": doc(["**/*.py"])}
        chosen, omitted = rules.select(docs, ["src/k.cu"], 10000)
        self.assertEqual([p for p, _ in chosen], ["paths/p.md", "cpp/z.md", "cuda/a.md"])
        self.assertEqual(omitted, [])
        self.assertEqual(chosen[0][1], rules.render_doc(docs["paths/p.md"]))
        size = len(chosen[0][1]) + len(chosen[1][1])
        chosen, omitted = rules.select(docs, ["src/k.cu"], size)
        self.assertEqual([p for p, _ in chosen], ["paths/p.md", "cpp/z.md"])
        self.assertEqual(omitted, ["cuda/a.md"])

    def test_write_indexes(self):
        rules.apply_change(self.repo, change(), "s")
        rules.apply_change(self.repo, change(path="python/a.md", applies=["**/*.py"]), "s")
        written = rules.write_indexes(self.repo)
        self.assertEqual(sorted(written), sorted([
            rules.RULES_DIR + "/INDEX.md", rules.RULES_DIR + "/cuda/INDEX.md",
            rules.RULES_DIR + "/python/INDEX.md"]))
        root = self.read(self.file("INDEX.md"))
        self.assertIn("cuda/", root)
        self.assertIn("python/", root)
        self.assertNotIn("shell/", root)
        self.assertIn("cuda/stream-sync.md", self.read(self.file("cuda/INDEX.md")))
        rules.apply_change(self.repo, change(op="remove", target=rules.rule_id(
            "cuda/stream-sync.md", "규칙 하나")), "s")
        rules.write_indexes(self.repo)
        self.assertFalse(os.path.exists(self.file("cuda/INDEX.md")))
        self.assertNotIn("cuda/", self.read(self.file("INDEX.md")))
        # the generated indexes are not loaded as guideline files
        self.assertEqual(list(rules.load_docs(self.repo)), ["python/a.md"])


if __name__ == "__main__":
    unittest.main()
