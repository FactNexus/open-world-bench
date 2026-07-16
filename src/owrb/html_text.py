"""Minimal HTML-to-text extraction for evaluator evidence (stdlib only).

Good enough for claim/citation judging: strips script/style/navigation-free
markup, keeps block structure as line breaks, and captures the page title.
A richer extractor (e.g. selectolax) can replace this behind the same
function signature later.
"""

from __future__ import annotations

from html.parser import HTMLParser

_SKIPPED_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "iframe"})
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "section",
        "article",
        "header",
        "footer",
        "li",
        "ul",
        "ol",
        "table",
        "tr",
        "br",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "pre",
    }
)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self.title: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED_TAGS:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title = (self.title or "") + data.strip()
            return
        self._chunks.append(data)

    def text(self) -> str:
        raw = "".join(self._chunks)
        lines = [" ".join(line.split()) for line in raw.splitlines()]
        return "\n".join(line for line in lines if line)


def extract_text(html: str) -> tuple[str, str | None]:
    """Return (visible text, title) for an HTML document."""
    extractor = _TextExtractor()
    extractor.feed(html)
    extractor.close()
    return extractor.text(), extractor.title or None
