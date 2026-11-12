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

    def test_check_path_trailing_newline_and_non_str(self):
        self.assertIsInstance(rules.check_path("cuda/x.md\n"), str)
        self.assertIsInstance(rules.check_path(["cuda/x.md"]), str)
        self.assertIsInstance(rules.check_path(5), str)

    def test_load_ignores_unsafe_names(self):
        self.put("cuda/ok.md", rules.render_doc(DOC))
        self.put("cuda/Bad Name.md", rules.render_doc(DOC))
        self.put("cuda/new\nline.md", rules.render_doc(DOC))
        self.assertEqual(list(rules.load_docs(self.repo)), ["cuda/ok.md"])

    def test_check_path(self):
        self.assertIsNone(rules.check_path("cuda/stream-sync.md"))
        for bad in ("../x.md", "cuda/Sync.md", "other/x.md", "cuda/a/b.md", "cuda/x.txt"):
            self.assertIsInstance(rules.check_path(bad), str, bad)

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



if __name__ == "__main__":
    unittest.main()
