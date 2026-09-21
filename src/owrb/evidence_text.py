"""Evidence text cleaning and claim-relevant passage selection for the judge.

Two defects motivated this module (found 2026-09-22 on a real run): pages fetched
through a markdown gateway arrived with the proxy's YAML front matter still attached,
and the judge was shown only the first 1,200 characters of each cited page. Between
them the judge saw an overview block and no fee table, no reviews, no body text at
all, and then guessed. The fixes: strip front matter on every load, and give the judge
passages selected for the claim being judged rather than the head of the page.
"""

from __future__ import annotations

import re

_FRONT_MATTER = re.compile(r"\A\ufeff?---[ \t]*\r?\n.*?\r?\n---[ \t]*\r?\n\s*", re.DOTALL)
_TOKEN = re.compile(r"[a-z0-9][a-z0-9'.\-]*")
_STOP = frozenset(
    [
        "the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "with", "by",
        "from", "is", "are", "was", "were", "be", "been", "it", "its", "this", "that",
        "these", "those", "as", "via", "per", "about", "into", "over", "under", "near",
        "not", "no", "can", "may", "will", "would", "should", "also", "very", "more",
        "most", "some", "any", "all", "each", "has", "have", "had",
    ]
)
_MIN_TOKEN = 2


def strip_front_matter(text: str) -> str:
    """Remove a leading YAML front-matter block (``---`` ... ``---``) if present."""
    match = _FRONT_MATTER.match(text)
    return text[match.end() :] if match else text


def clean_evidence_text(text: str) -> str:
    """Front matter stripped, line endings normalised, blank runs collapsed."""
    cleaned = strip_front_matter(text).replace("\r\n", "\n")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _tokens(text: str) -> set[str]:
    out: set[str] = set()
    for raw in _TOKEN.findall(text.lower()):
        token = raw.strip(".'-")
        if len(token) < _MIN_TOKEN or token in _STOP:
            continue
        out.add(token)
        # "44.00" also matches "44"; "2026-07" also matches "2026".
        head = re.split(r"[.\-]", token)[0]
        if head != token and head.isdigit():
            out.add(head)
    return out


def _is_table_line(line: str) -> bool:
    return line.lstrip().startswith("|")


def split_blocks(text: str, max_block_chars: int = 700) -> list[str]:
    """Paragraph-sized blocks in document order.

    Blank lines separate blocks; an oversized block is split on line boundaries.
    When a markdown table is split, each piece repeats the table's header rows so a
    fee row still reads as a fee row on its own.
    """
    blocks: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip("\n")
        if not paragraph.strip():
            continue
        if len(paragraph) <= max_block_chars:
            blocks.append(paragraph)
            continue
        lines = paragraph.split("\n")
        header: list[str] = []
        if len(lines) >= 2 and _is_table_line(lines[0]) and _is_table_line(lines[1]):
            header = lines[:2]
        current: list[str] = []
        size = 0
        for line in lines:
            if current and size + len(line) + 1 > max_block_chars:
                blocks.append("\n".join(current))
                current = [*header]
                size = sum(len(h) + 1 for h in current)
            current.append(line)
            size += len(line) + 1
        if current and "\n".join(current) != "\n".join(header):
            blocks.append("\n".join(current))
    return blocks


def select_passages(
    text: str,
    queries: list[str],
    max_chars: int = 4000,
    head_chars: int = 400,
) -> str:
    """Return up to ``max_chars`` of ``text`` chosen for relevance to ``queries``.

    The head of the page (title, overview) is always kept; the rest of the budget
    goes to the blocks sharing the most tokens with the queries, rendered in document
    order with ``[…]`` where blocks were skipped. A page that fits the budget is
    returned whole, cleaned.
    """
    cleaned = clean_evidence_text(text)
    if len(cleaned) <= max_chars:
        return cleaned
    blocks = split_blocks(cleaned)
    if not blocks:
        return cleaned[:max_chars]
    query_tokens: set[str] = set()
    for query in queries:
        query_tokens |= _tokens(query)

    chosen: set[int] = set()
    budget = max_chars
    used = 0
    for index, block in enumerate(blocks):
        if used >= head_chars:
            break
        chosen.add(index)
        used += len(block) + 1
    budget -= used

    scored = []
    for index, block in enumerate(blocks):
        if index in chosen:
            continue
        overlap = len(query_tokens & _tokens(block))
        if overlap:
            scored.append((-overlap, index))
    scored.sort()
    for _score, index in scored:
        length = len(blocks[index]) + 1
        if length > budget:
            continue
        chosen.add(index)
        budget -= length
    if budget > 200:  # fill leftover budget in document order so context is not lost
        for index, block in enumerate(blocks):
            if index in chosen:
                continue
            length = len(block) + 1
            if length > budget:
                continue
            chosen.add(index)
            budget -= length

    rendered: list[str] = []
    previous = -1
    for index in sorted(chosen):
        if previous >= 0 and index != previous + 1:
            rendered.append("[…]")
        rendered.append(blocks[index])
        previous = index
    if previous < len(blocks) - 1:
        rendered.append("[…]")
    return "\n\n".join(rendered)
