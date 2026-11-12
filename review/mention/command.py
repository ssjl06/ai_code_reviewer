"""Reading a comment: is the bot mentioned, which slash command follows, which issue ids."""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

COMMANDS = {"/철회": "withdraw", "/지침": "rule-add", "/지침수정": "rule-update",
            "/지침삭제": "rule-remove"}

_FENCE_OPEN = re.compile(r"^[ \t]*(`{3,}|~{3,})")
_QUOTE = re.compile(r"^[ \t]*(?:(?:[-*+]|\d+[.)])[ \t]+)*>")
_HEADING = re.compile(r"^ {0,3}#{1,6}(?:[ \t\r]|$)")
# a list item start (0-3 spaces: a marker indented further is a lazy line of the quote)
_ITEM = re.compile(r"^ {0,3}(?:[-*+]|\d+[.)])(?:[ \t\r]|$)")
_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.S)
_SPAN = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.S)
_GONE = "\ufffc"  # stands for a removed span: not whitespace, cannot start a command
_ID = re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{8}(?![0-9A-Za-z])")


def _mention_re(bot: str):
    return re.compile(r"(?<![0-9A-Za-z_-])@%s(?![0-9A-Za-z_-])" % re.escape(bot), re.I)


def _strip_blocks(body: str) -> str:
    """Without fenced blocks, indented code, quote blocks and HTML comments. A quote block
    is a quote line and, as GitHub renders it (lazy continuation), every following non-blank
    line up to a blank line, a fence start, a heading or a list item start."""
    out, fence, prev_blank, prev_code, quote = [], None, True, False, False
    for line in (body or "").split("\n"):
        if fence:
            m = re.match(r"^[ \t]*(%s{%d,})[ \t]*\r?$" % (re.escape(fence[0]), len(fence)), line)
            if m:
                fence = None
            out.append("")
            continue
        m = _FENCE_OPEN.match(line)
        if m:
            fence = m.group(1)
            out.append("")
            prev_blank = prev_code = quote = False
            continue
        blank = line.strip() == ""
        if _QUOTE.match(line):
            quote = True
        elif blank or _HEADING.match(line) or _ITEM.match(line):
            quote = False
        code = line.startswith(("    ", "\t")) and not blank and (prev_blank or prev_code) \
            and not quote
        if code or quote:
            out.append("")
        else:
            out.append(line)
        prev_code = code
        prev_blank = blank
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
