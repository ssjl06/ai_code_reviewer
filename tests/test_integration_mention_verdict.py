"""Mention verdicts end to end: a state that is not current for the head, a waived issue
reported again, a ledger too big for one review."""
import unittest

from review.render import parse_state
from tests.mention_case import BLOCKER, MentionBase, answer

MINOR = dict(BLOCKER, file="src/u.cu", line=1, severity="minor", title="전역 변수 이름")
TWO = {"summary": "2건", "findings": [BLOCKER, MINOR], "previous_findings": [],
       "incomplete_reason": None, "diff_reviewed": True}
NEW_BLOCKER = dict(BLOCKER, file="src/u.cu", line=1, title="전역 변수 초기화 순서")


class SettleTest(MentionBase):
    def settle(self, unclear):
        """Review 1 blocks on A; a crafted review 2 adds blocker B with `unclear` (absent:
        an old marker); a person dismisses review 1 (A waived): the next run settles."""
        st = self.reviewed()
        b = dict(st["issues"][0], id="0000000b", line=1, lo=1, hi=1)
        crafted = dict(st, issues=[b] + st["issues"])
        crafted.pop("unclear", None)
        if unclear is not None:
            crafted["unclear"] = unclear
        self.put_state(crafted, event="CHANGES_REQUESTED")
        self.gh.add_dismissal(1, "alice")
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        self.assertIn("면제 반영", self.stdout)
        return self.gh.posted[-1]

    def test_unclear_blocker_stays_unclear(self):
        last = self.settle(["0000000b"])
        self.assertEqual(last["event"], "COMMENT")
        self.assertEqual(parse_state(last["body"])["unclear"], ["0000000b"])

    def test_old_marker_stays_unknown(self):
        last = self.settle(None)
        self.assertEqual(last["event"], "COMMENT")
        self.assertNotIn("unclear", parse_state(last["body"]))


class StaleStateTest(MentionBase):
    def stale(self):
        """Blocker A and minor B reviewed at C1; head H2 adds a file nobody reviewed (the
        review phase does not run on a draft)."""
        st = self.reviewed(TWO)
        self.commit_and_push({"src/v.cu": "int v = 0;\n"})
        self.gh.draft = True
        return {i["title"]: i["id"] for i in st["issues"]}

    def test_waiver_on_stale_state_comments(self):
        ids = self.stale()
        self.mention("@ai-bot /철회 %s 의도된 이름" % ids[MINOR["title"]])
        self.script(mention=[{"out": answer("철회합니다."), "withdraw_all": True}])
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        last = self.gh.posted[-1]
        self.assertEqual(parse_state(last["body"])["m"]["w"], [ids[MINOR["title"]]])
        self.assertEqual(last["event"], "COMMENT")

    def test_new_blocker_on_stale_state_requests_changes(self):
        self.stale()
        self.mention("@ai-bot 1번 줄 초기화 순서 괜찮아?")
        self.script(mention=[{"out": answer("문제입니다.", new_issues=[NEW_BLOCKER])}])
        self.assertEqual(self.run_phases(), 0, self.stdout + self.stderr)
        last = self.gh.posted[-1]
        self.assertEqual(len(parse_state(last["body"])["m"]["n"]), 1)
        self.assertEqual(last["event"], "REQUEST_CHANGES")


if __name__ == "__main__":
    unittest.main()
