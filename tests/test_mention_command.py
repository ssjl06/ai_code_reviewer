import unittest

from review.mention import command as cmd

BOT = "ai-bot"


class CommandTest(unittest.TestCase):
    def cmd(self, body):
        return cmd.parse(body, BOT)[0]

    def test_focus_1_cases(self):
        self.assertIsNone(self.cmd("@ai-bot 철회 안 해도 돼, 진짜 버그야"))
        self.assertIsNone(self.cmd("@ai-bot 철회"))
        self.assertEqual(self.cmd("@AI-BOT /철회 테스트 코드라 의도된 동작"), "withdraw")
        self.assertFalse(cmd.mentions_bot("> @ai-bot /철회", BOT))
        self.assertFalse(cmd.mentions_bot("`@ai-bot /철회`", BOT))
        self.assertEqual(self.cmd("@ai-bot /철회?"), "unknown")

    def test_other_commands(self):
        self.assertEqual(self.cmd("@ai-bot /지침 x"), "rule-add")
        self.assertEqual(self.cmd("@ai-bot /지침수정 x"), "rule-update")
        self.assertEqual(self.cmd("@ai-bot /지침삭제 x"), "rule-remove")
        self.assertEqual(self.cmd("@ai-bot /철휘"), "unknown")

    def test_position(self):
        self.assertIsNone(self.cmd("@ai-bot\n/철회"))
        self.assertIsNone(self.cmd("/철회 @ai-bot 왜"))

    def test_mention_boundaries(self):
        self.assertTrue(cmd.mentions_bot("@ai-bot님 안녕", BOT))
        self.assertFalse(cmd.mentions_bot("@ai-bot-2 hi", BOT))
        self.assertFalse(cmd.mentions_bot("x@ai-bot hi", BOT))

    def test_ids(self):
        ids = lambda b: cmd.parse(b, BOT)[1]
        self.assertEqual(ids("@ai-bot /철회 a1b2c3d4는"), ["a1b2c3d4"])
        self.assertEqual(ids("@ai-bot /철회 `a1b2c3d4` a1b2c3d4 0badf00d"),
                         ["a1b2c3d4", "0badf00d"])
        self.assertEqual(ids("@ai-bot xa1b2c3d4"), [])
        self.assertEqual(ids("@ai-bot\n```\na1b2c3d4\n```"), [])
        self.assertEqual(ids("@ai-bot\n> a1b2c3d4"), [])


if __name__ == "__main__":
    unittest.main()
