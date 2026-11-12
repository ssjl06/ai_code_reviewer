"""Targets of a `/철회` mention (spec 5.1)."""
import unittest

from review.mention import context as cx
from review.render import issue_marker
from tests.test_mention_context import BOT, iss, mention, root


class TargetsTest(unittest.TestCase):
    def test_marker(self):
        t = [root("**[x]** 다른 제목\n\n본문" + issue_marker("bbbb2222"))]
        issues = [iss("aaaa1111"), iss("bbbb2222")]
        self.assertEqual([i["id"] for i in cx.targets(mention(), t, issues, BOT)], ["bbbb2222"])

    def test_fallback_by_path_line_title(self):
        t = [root("**[중대 · 확신 90 · 버그]** 널 참조\n\n본문")]
        issues = [iss("aaaa1111", title="다른"), iss("bbbb2222")]
        self.assertEqual([i["id"] for i in cx.targets(mention(), t, issues, BOT)], ["bbbb2222"])

    def test_bot_thread_unknown_issue_never_falls_back(self):
        t = [root("**[x]** 제목" + issue_marker("zzzz9999"))]
        self.assertEqual(cx.targets(mention(), t, [iss("aaaa1111")], BOT), [])
        m = mention(ids=["aaaa1111"])
        self.assertEqual([i["id"] for i in cx.targets(m, t, [iss("aaaa1111")], BOT)], ["aaaa1111"])

    def test_bot_thread_closed_issue_returned(self):
        t = [root("**[x]** 제목" + issue_marker("aaaa1111"))]
        got = cx.targets(mention(), t, [iss("aaaa1111", status="waived"), iss("bbbb2222")], BOT)
        self.assertEqual([i["id"] for i in got], ["aaaa1111"])

    def test_fallback_needs_exact_title(self):
        t = [root("**[중대 · 확신 90 · 버그]** 널 참조 가능성\n\n본문")]
        self.assertEqual(cx.targets(mention(), t, [iss("aaaa1111"), iss("bbbb2222", line=9)], BOT), [])

    def test_human_root_is_not_a_bot_issue(self):
        t = [root("**[x]** 널 참조", user="carol")]
        issues = [iss("aaaa1111"), iss("bbbb2222", line=9)]
        self.assertEqual(cx.targets(mention(), t, issues, BOT), [])

    def test_ids(self):
        m = mention(kind="general", ids=["bbbb2222", "cccc3333"])
        issues = [iss("aaaa1111"), iss("bbbb2222"), iss("cccc3333", status="resolved")]
        got = cx.targets(m, [], issues, BOT)  # a closed id named is a target (apply drops it)
        self.assertEqual([i["id"] for i in got], ["bbbb2222", "cccc3333"])

    def test_waived_id_named_is_the_target_not_the_open_one(self):
        m = mention(kind="general", ids=["aaaa1111"])
        issues = [iss("aaaa1111", status="waived"), iss("bbbb2222")]
        self.assertEqual([i["id"] for i in cx.targets(m, [], issues, BOT)], ["aaaa1111"])

    def test_waived_id_named_with_one_other_open_issue(self):
        m = mention(kind="general", ids=["aaaa1111"])
        issues = [iss("aaaa1111", status="waived"), iss("bbbb2222"),
                  iss("cccc3333", status="resolved")]
        self.assertEqual([i["id"] for i in cx.targets(m, [], issues, BOT)], ["aaaa1111"])

    def test_hex_word_that_is_no_issue_id_keeps_the_single_open_rule(self):
        m = mention(kind="general", ids=["deadbeef"])
        issues = [iss("aaaa1111", status="waived"), iss("bbbb2222")]
        self.assertEqual([i["id"] for i in cx.targets(m, [], issues, BOT)], ["bbbb2222"])

    def test_single_open(self):
        m = mention(kind="general")
        issues = [iss("aaaa1111", status="waived"), iss("bbbb2222")]
        self.assertEqual([i["id"] for i in cx.targets(m, [], issues, BOT)], ["bbbb2222"])

    def test_none_with_two_open(self):
        m = mention(kind="general")
        self.assertEqual(cx.targets(m, [], [iss("aaaa1111"), iss("bbbb2222")], BOT), [])

    def test_only_withdraw(self):
        self.assertEqual(cx.targets(mention(command=None), [], [iss("aaaa1111")], BOT), [])


if __name__ == "__main__":
    unittest.main()
