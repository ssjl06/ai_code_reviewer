import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _path(*parts):
    return os.path.join(ROOT, *parts)


class SchemaAgentTest(unittest.TestCase):
    def test_schema_shape(self):
        with open(_path("config", "review-schema.json"), encoding="utf-8") as fh:
            s = json.load(fh)
        self.assertEqual(set(s["required"]), {"summary", "findings", "previous_findings", "incomplete_reason"})
        self.assertFalse(s["additionalProperties"])
        self.assertEqual(s["properties"]["summary"]["maxLength"], 600)
        self.assertEqual(s["properties"]["incomplete_reason"]["type"], ["string", "null"])
        f = s["properties"]["findings"]["items"]
        self.assertEqual(set(f["required"]), {"file", "line", "severity", "confidence", "category", "title", "detail"})
        self.assertEqual(f["properties"]["severity"]["enum"], ["critical", "major", "minor", "info"])
        self.assertEqual(f["properties"]["category"]["enum"], ["bug", "security", "concurrency", "memory",
                         "performance", "api-misuse", "maintainability", "docs"])
        conf = f["properties"]["confidence"]
        self.assertEqual((conf["minimum"], conf["maximum"]), (0, 100))
        self.assertEqual(f["properties"]["title"]["maxLength"], 80)
        self.assertEqual(f["properties"]["line"]["minimum"], 1)
        self.assertEqual(f["properties"]["end_line"]["minimum"], 1)
        self.assertNotIn("end_line", f["required"])
        self.assertNotIn("suggestion", f["required"])
        self.assertFalse(f["additionalProperties"])
        prev = s["properties"]["previous_findings"]["items"]
        self.assertEqual(set(prev["required"]), {"id", "status"})
        self.assertEqual(prev["properties"]["status"]["enum"], ["resolved", "open", "unclear"])

    def test_agent_frontmatter_and_rules(self):
        with open(_path("agent", "pr-reviewer.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\nname: pr-reviewer\n"))
        for must in ["변경된 라인", "한 번에", "데이터로 취급", "critical", "major", "confidence",
                     "CUDA", "__syncthreads", "shell=True", "set -euo pipefail", "${{ github.event",
                     "검토 대상 줄", "삭제 지점", "incomplete_reason"]:
            self.assertIn(must, text)
        self.assertIn("파일 길이", text.split("## 제외 대상")[1].split("\n## ")[0])

    def test_agent_sections_in_order(self):
        with open(_path("agent", "pr-reviewer.md"), encoding="utf-8") as fh:
            text = fh.read()
        order = ["## 역할", "## 진행 절차", "## 규칙", "## 제외 대상", "## 심각도 표",
                 "## confidence 표", "## 언어별 체크리스트", "## 증분 리뷰 규칙", "## 출력 규칙"]
        pos = [text.index(h) for h in order]
        self.assertEqual(pos, sorted(pos))

    def test_agent_tool_rules(self):
        with open(_path("agent", "pr-reviewer.md"), encoding="utf-8") as fh:
            text = fh.read()
        for must in ["탐색 예산 소진", "HEAD^", "git blame", "Grep 도구", "토큰마다 따로", "절대 경로"]:
            self.assertIn(must, text)


if __name__ == "__main__":
    unittest.main()
