"""Merge chunk findings and compute the deterministic review verdict."""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Set, Tuple

from review.ledger import (SEV_RANK, classify, finding_id, finish_orphans,  # noqa: F401
                           unique_id)  # (finding_id re-exported)

OK_STATUSES = ("ok", "partial")  # chunks whose output is used; only "ok" counts as reviewed
STATUS_RANK = {"open": 0, "unclear": 1, "resolved": 2}
_ROOT = re.compile(r"^(?:\./|/)+")
_DIFF_PREFIX = re.compile(r"^[ab]/")


def normalize_path(p: str, known: Optional[Set[str]] = None) -> str:
    """Strip `./` and `/`; strip one `a/`/`b/` diff prefix unless the path is a known file."""
    raw = _ROOT.sub("", p)
    return raw if known and raw in known else _DIFF_PREFIX.sub("", raw)


def _sev(f: dict) -> int:
    return SEV_RANK.get(f.get("severity"), len(SEV_RANK))


def _strength(f: dict) -> Tuple[int, int]:
    return (_sev(f), -f.get("confidence", 0))


def _resolve_path(f: dict, own: Set[str], known: Optional[Set[str]],
                  changed: Optional[Dict[str, Set[int]]]) -> str:
    raw = _ROOT.sub("", f["file"])
    cands = list(dict.fromkeys((raw, _DIFF_PREFIX.sub("", raw))))
    for pool in (own, known):
        hit = [c for c in cands if pool and c in pool]
        if len(hit) > 1 and changed is not None:
            hit = [c for c in hit if f.get("line") in changed.get(c, set())] or hit
        if hit:
            return hit[0]
    return cands[-1]


def _collect(results: List[dict], cfg: dict, changed: Optional[Dict[str, Set[int]]],
             chunk_files=None):
    """Paths resolve against the reporting chunk's own files first (a literal `b/x` and `x`
    may sit in different chunks), then against the whole PR."""
    known = set(changed) if changed is not None else (
        {f for fs in chunk_files.values() for f in fs} if chunk_files else None)
    kept, dropped = [], 0
    for r in results:
        if r.get("status") not in OK_STATUSES or not r.get("output"):
            continue
        own = set(chunk_files.get(r["index"], ())) if chunk_files is not None else set()
        for raw in r["output"].get("findings", []):
            f = dict(raw)
            f["file"] = _resolve_path(f, own, known, changed)
            if f.get("confidence", 0) < cfg["min_confidence"]:
                dropped += 1
            elif changed is not None and f.get("line") not in changed.get(f["file"], set()):
                dropped += 1
            else:
                kept.append(f)
    return kept, dropped


def _near(f: dict, g: dict) -> bool:
    return (g["file"] == f["file"] and g.get("category") == f.get("category")
            and abs(g["line"] - f["line"]) <= 2)


def _copy(f: dict, g: dict) -> bool:
    return all(f.get(k) == g.get(k) for k in ("file", "line", "title", "same_as"))


def _dedupe(findings: List[dict]) -> Tuple[List[dict], List[dict]]:
    """`(kept, removed)`, from findings whose `blocking` is set. A blocking finding is removed
    only as a copy of a kept blocking one (same file, line, title and `same_as`: the stronger
    is kept, ties keep the earlier one), never as a near duplicate: only the model's
    `same_as` may merge it. A non-blocking finding near a kept blocking one (same file and
    category, within 2 lines) is removed into it; otherwise of near non-blocking findings the
    strongest is kept (ties keep the earlier one). A removed finding on another line than the
    kept one, or with another title, is `folded` (`dup_of` names the kept one's place in this
    run); one on the same line with the same title is a copy from overlapping chunks and
    carries no `folded`. The kept ones keep their input order."""
    order = {id(f): n for n, f in enumerate(findings)}
    out: List[dict] = []  # blocking slots first, then the non-blocking ones
    lost: List[Tuple[dict, int]] = []  # (removed finding, slot of the kept one)
    for f in [f for f in findings if f.get("blocking")]:
        i = next((i for i, g in enumerate(out) if _copy(f, g)), None)
        if i is None:
            out.append(f)
            continue
        if _strength(f) < _strength(out[i]):  # ties keep the earlier one
            out[i], f = f, out[i]
        lost.append((f, i))
    hard = len(out)
    for f in [f for f in findings if not f.get("blocking")]:
        i = next((i for i, g in enumerate(out[:hard]) if _near(f, g)), None)
        if i is None:
            i = next((i for i, g in enumerate(out) if i >= hard and _near(f, g)), None)
            if i is None:
                out.append(f)
                continue
            if _strength(f) < _strength(out[i]):
                out[i], f = f, out[i]
        lost.append((f, i))
    removed = []
    for f, i in lost:
        k = out[i]
        if f["line"] == k["line"] and f.get("title") == k.get("title"):
            removed.append(dict(f))
        else:
            removed.append(dict(f, folded=True, dup_of="이번 실행 %s:%s" % (k["file"], k["line"])))
    return sorted(out, key=lambda f: order[id(f)]), removed


def _resolve(prev: dict) -> str:
    """Status of a previous finding the model did not report, from `previous.annotate`."""
    pre = prev.get("pre")
    if pre == "resolved":
        return "resolved"
    if pre == "ask" and not prev.get("located"):
        return "unclear"
    return "open"


def _previous(results: List[dict], previous: List[dict], chunk_files=None,
              shown=None, skipped=(), truncated=(), recheck_only=()):
    """Model reports win over `_resolve`. With `shown`, a report counts only from a chunk that
    showed the whole region of the issue (a chunk that did not is ignored, and the issue falls
    to `_resolve`); None: not checked. "resolved" counts only from an ok chunk that holds the
    file, only when every chunk holding that file is ok (None: chunks hold all files), and the
    file is neither skipped nor truncated. A file in `recheck_only` (a target only for
    rechecks: none of its lines is to be reviewed) may be skipped or truncated: its item
    shows the issue's lines, and `shown` decides."""
    status = {r["index"]: r.get("status") for r in results}
    holds = lambda i, path: chunk_files is None or path in chunk_files.get(i, ())
    files = {p["id"]: p["file"] for p in previous}
    spans = {p["id"]: range(p.get("lo", p["line"]), p.get("hi", p["line"]) + 1)
             for p in previous}

    def saw(index, pid, path):
        return shown is None or set(spans[pid]) <= shown.get(index, {}).get(path, set())
    reported: Dict[str, Tuple[str, Optional[str]]] = {}
    for r in results:
        if r.get("status") not in OK_STATUSES or not r.get("output"):
            continue
        for p in r["output"].get("previous_findings", []):
            path = files.get(p["id"], "")
            if p["id"] not in spans or not saw(r["index"], p["id"], path):
                continue
            if p["status"] == "resolved" and not (
                    r["status"] == "ok" and holds(r["index"], path)
                    and all(st == "ok" for i, st in status.items() if holds(i, path))
                    and (path in recheck_only
                         or (path not in skipped and path not in truncated))):
                continue
            cur = reported.get(p["id"])
            if cur is None or STATUS_RANK[p["status"]] < STATUS_RANK[cur[0]]:
                reported[p["id"]] = (p["status"], p.get("note"))
    out = []
    for prev in previous:
        e = dict(prev)
        if prev["id"] in reported:
            e["status"] = reported[prev["id"]][0]
            if reported[prev["id"]][1] is not None:
                e["note"] = reported[prev["id"]][1]
        else:
            e["status"] = _resolve(prev)
        out.append(e)
    return out


def _apply_links(prev_out: List[dict], links: List[dict]) -> None:
    """A valid `same_as` link is open evidence for its issue, in place: an entry the model
    reported resolved in this run is open again (`reopened` set on it and on the link; one of
    a file that left the PR, `pre == "resolved"`, stays resolved), and a blocking link makes
    it blocking."""
    by_id = {e["id"]: e for e in prev_out}
    for link in links:
        e = by_id.get(link["id"])
        if e is None:
            continue
        if e.get("status") == "resolved" and e.get("pre") != "resolved":
            e["status"], e["reopened"], link["reopened"] = "open", True, True
        if link.get("blocking") and not e.get("blocking"):
            e["blocking"] = True


def _evidence(findings: List[dict]) -> List[dict]:
    """Non-blocking pseudo-links for each issue a finding named in an ignored `same_as` (the
    finding is more severe than every issue in reach): the model still said the problem is
    there, so `_apply_links` reopens an entry reported resolved in this run, and never
    changes its blocking. They are not links: no orphan moves and no link is logged."""
    out: List[dict] = []
    for f in findings:
        if f.get("same_as_ignored"):
            e = {"id": f["same_as_ignored"], "file": f["file"], "line": f["line"],
                 "blocking": False}
            if e not in out:
                out.append(e)
    return out


def _summaries(results: List[dict]) -> List[str]:
    out = []
    for r in sorted(results, key=lambda x: x["index"]):
        if r.get("status") not in OK_STATUSES or not r.get("output"):
            continue
        o = r["output"]
        line = "[c%d] %s" % (r["index"] + 1, o.get("summary", ""))
        if o.get("incomplete_reason"):
            line += " (참고: %s)" % o["incomplete_reason"]
        out.append(line)
    return out


def merge(results, cfg, previous, changed, no_targets, skipped=None, anchor=None,
          chunk_files: Optional[Dict[int, List[str]]] = None, truncated=None,
          closed: Optional[List[dict]] = None,
          shown: Optional[Dict[int, Dict[str, Set[int]]]] = None,
          recheck_only: Optional[Set[str]] = None, left_out: Optional[List[str]] = None,
          cut: Optional[Iterable[str]] = None):
    """The verdict of a run. A skipped or truncated file in `recheck_only` (a target only for
    rechecks) neither refuses `resolved` nor makes the run incomplete, unless a recheck item
    was left out (`left_out`, from `ledger.assign_recheck`): then every skipped or truncated
    file counts as today. `gaps` lists the skipped and truncated files that count. `cut` lists
    the files with a line too long for a chunk (cut in this run or earlier and still in the
    head): never an approval, but the run stays complete (the line counts as reviewed)."""
    kept, dropped = _collect(results, cfg, changed, chunk_files)
    for f in kept:
        in_block = f.get("severity") in cfg["block_severities"]
        if anchor is not None:
            f["anchor"] = anchor(f["file"], f["line"])
        f["blocking"] = in_block and f["confidence"] >= cfg["block_confidence"]
        f["needs_check"] = in_block and not f["blocking"]
    findings, removed = _dedupe(kept)  # needs `blocking`
    # previous entries come from previous.annotate, so their "file" is already the renamed path
    known = list(previous) + list(closed or [])
    findings, duplicates, links = classify(findings, known)
    # a finding `_dedupe` removed is still evidence: keep its links (its own listing stays)
    gone_new, gone_dups, gone_links = classify(removed, known)
    links += [x for x in gone_links if x not in links]
    evidence = _evidence(findings + duplicates + gone_new + gone_dups)
    skipped, truncated = list(skipped or []), list(truncated or [])
    only = set(recheck_only or ())
    prev_out = _previous(results, previous, chunk_files, shown, skipped, truncated, only)
    _apply_links(prev_out, links)
    _apply_links(prev_out, evidence)
    folded = [d for d in duplicates if d.get("folded")] + [f for f in removed if f.get("folded")]
    overlap = [f for f in removed if not f.get("folded")]
    taken = {i["id"] for i in known}
    for f in findings:
        f["id"] = unique_id(f, taken)
        taken.add(f["id"])
    exempt = set() if left_out else only
    gaps = [p for p in skipped + truncated if p not in exempt]
    incomplete = sorted(r["index"] for r in results if r.get("status") != "ok")
    errors = [[r["index"], str(r.get("error") or r.get("status"))[:200]]
              for r in sorted(results, key=lambda x: x["index"]) if r.get("status") != "ok"]
    failed_all = bool(results) and all(r.get("status") == "failed" for r in results)
    complete = not (incomplete or gaps or failed_all)
    finish_orphans(prev_out, links, complete)
    unclear_blocking = any(p.get("blocking") and p["status"] == "unclear" for p in prev_out)
    cut_files = sorted(set(cut or ()))
    blocking = any(f["blocking"] for f in findings) or any(
        p.get("blocking") and p["status"] == "open" for p in prev_out)
    if blocking:
        event = "REQUEST_CHANGES"
    elif no_targets or incomplete or failed_all or gaps or unclear_blocking or cut_files:
        event = "COMMENT"
    else:
        event = "APPROVE"
    findings.sort(key=lambda f: (_sev(f), -f["confidence"], f["file"], f["line"]))
    return {"event": event, "findings": findings, "previous": prev_out,
            "duplicates": duplicates, "links": links, "evidence": evidence,
            "folded": folded, "overlap": overlap,
            "incomplete": incomplete, "errors": errors, "failed_all": failed_all,
            "no_targets": no_targets, "summaries": _summaries(results),
            "dropped": dropped, "skipped": skipped, "unclear_blocking": unclear_blocking,
            "truncated": truncated, "gaps": gaps, "cut": cut_files,
            "complete": complete}


def split_inline(findings, right_lines):
    inline, body = [], []
    for f in findings:
        (inline if f["line"] in right_lines.get(f["file"], ()) else body).append(f)
    return inline, body
