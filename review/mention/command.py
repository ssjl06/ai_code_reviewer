"""Reading a comment: is the bot mentioned, which slash command follows, which issue ids."""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

COMMANDS = {"/철회": "withdraw", "/지침": "rule-add", "/지침수정": "rule-update",
            "/지침삭제": "rule-remove"}

_FENCE = re.compile(r"```.*?(?:```|\Z)", re.S)
_INLINE = re.compile(r"`[^`\n]*`")
_QUOTE = re.compile(r"^[ \t]*>.*$", re.M)
_ID = re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{8}(?![0-9A-Za-z])")


def _mention_re(bot: str):
    return re.compile(r"(?<![0-9A-Za-z_-])@%s(?![0-9A-Za-z_-])" % re.escape(bot), re.I)


def _strip_blocks(body: str) -> str:
    return _QUOTE.sub("", _FENCE.sub("", body or ""))


def clean(body: str) -> str:
    """`body` without fenced blocks, inline code and lines starting with `>`."""
    return _INLINE.sub("", _strip_blocks(body))


def mentions_bot(body: str, bot: str) -> bool:
    return _mention_re(bot).search(clean(body)) is not None


def parse(body: str, bot: str) -> Tuple[Optional[str], List[str]]:
    """(command, ids): the command is the word right after the first mention, on its line."""
    text = clean(body)
    m = _mention_re(bot).search(text)
    command = None
    if m:
        w = re.match(r"[ \t]*(\S+)", text[m.end():])
        word = w.group(1) if w else ""
        if word in COMMANDS:
            command = COMMANDS[word]
        elif word.startswith("/"):
            command = "unknown"
    ids = list(dict.fromkeys(_ID.findall(_strip_blocks(body))))
    return command, ids
