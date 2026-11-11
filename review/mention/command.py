"""Reading a comment: is the bot mentioned, which slash command follows, which issue ids."""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

COMMANDS = {"/철회": "withdraw", "/지침": "rule-add", "/지침수정": "rule-update",
            "/지침삭제": "rule-remove"}

_FENCE_OPEN = re.compile(r"^[ \t]*(`{3,}|~{3,})")
_QUOTE = re.compile(r"^[ \t]*(?:(?:[-*+]|\d+[.)])[ \t]+)*>")
_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.S)
_SPAN = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.S)
_GONE = "\ufffc"  # stands for a removed span: not whitespace, cannot start a command
_ID = re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{8}(?![0-9A-Za-z])")


def _mention_re(bot: str):
    return re.compile(r"(?<![0-9A-Za-z_-])@%s(?![0-9A-Za-z_-])" % re.escape(bot), re.I)


def _strip_blocks(body: str) -> str:
    """Without fenced blocks, indented code, quote lines and HTML comments."""
    out, fence, prev_blank, prev_code = [], None, True, False
    for line in (body or "").split("\n"):
        if fence:
            m = re.match(r"^[ \t]*(%s{%d,})[ \t]*$" % (re.escape(fence[0]), len(fence)), line)
            if m:
                fence = None
            out.append("")
            continue
        m = _FENCE_OPEN.match(line)
        if m:
            fence = m.group(1)
            out.append("")
            prev_blank = prev_code = False
            continue
        code = line.startswith(("    ", "\t")) and line.strip() != "" and (prev_blank or prev_code)
        if code or _QUOTE.match(line):
            out.append("")
        else:
            out.append(line)
        prev_code = code
        prev_blank = line.strip() == ""
    return _COMMENT.sub(_GONE, "\n".join(out))


def clean(body: str) -> str:
    """`body` without code (fenced, indented, spans), HTML comments and quoted lines."""
    return _SPAN.sub(_GONE, _strip_blocks(body))


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
