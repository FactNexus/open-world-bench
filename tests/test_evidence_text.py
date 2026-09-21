"""Evidence cleaning and claim-relevant passage selection."""

from __future__ import annotations

from owrb.evidence_text import (
    clean_evidence_text,
    select_passages,
    split_blocks,
    strip_front_matter,
)

FRONT_MATTER = (
    "---\nversion: 1.0.0\nsource_url: http://example.internal/1.md\nfetched_at: 2026-09-20\n"
    "content:\n  title: Untitled\nprovenance:\n  generator:\n    name: md-proxy\n---\n\n"
)


def _wikicamps_page(filler_paragraphs: int = 12) -> str:
    filler = "\n\n".join(
        f"Review {n}: lovely spot, quiet at night, the road in was fine for a two-wheel drive. "
        "We stayed two nights and would come back." for n in range(filler_paragraphs)
    )
    return (
        "# Mary Pool Rest Area\n\n## Overview\n- **Type**: Roadside Rest Area\n- **Status**: Open\n"
        "- **Fee category**: Free\n- **State**: Western Australia\n\n"
        "## Facilities & Features\n- Toilets, Shade, Dogs Allowed\n\n"
        f"{filler}\n\n"
        "## Fees (as reported by WikiCamps users)\n\n"
        "| Item | Price | Reported | Votes |\n|---|---|---|---|\n"
        "| Free | AUD 0.00 | 2025-07 | 3 up / 0 down |\n"
        "| Donation | AUD 5.00 | 2024-08 | 1 up / 0 down |\n\n"
        "## Recent reviews\n\n- **2025-07** — Free stop, clean drop toilets, lots of shade.\n"
    )


def test_front_matter_is_stripped_only_when_leading() -> None:
    assert strip_front_matter(FRONT_MATTER + "# Title\nbody").startswith("# Title")
    body = "# Title\n\n---\nnot front matter\n---\n"
    assert strip_front_matter(body) == body
    assert clean_evidence_text("\n\n\n# T\n\n\n\nbody\n\n\n") == "# T\n\nbody"


def test_short_pages_are_returned_whole_and_cleaned() -> None:
    page = FRONT_MATTER + "# Title\n\nSome body text."
    assert select_passages(page, ["Some body"], max_chars=4000) == "# Title\n\nSome body text."


def test_fee_claim_selects_the_fee_table_beyond_the_head() -> None:
    page = FRONT_MATTER + _wikicamps_page()
    assert len(page) > 1500
    selected = select_passages(
        page,
        ["Mary Pool Rest Area is free, price reported July 2025 (AUD 0.00)"],
        max_chars=1500,
        head_chars=300,
    )
    assert selected.startswith("# Mary Pool Rest Area"), "the head of the page is always kept"
    assert "| Free | AUD 0.00 | 2025-07 |" in selected, "the fee row is selected for a fee claim"
    assert "[…]" in selected, "skipped material is marked"
    assert "version: 1.0.0" not in selected
    assert len(selected) <= 1500 + 50


def test_split_tables_repeat_their_header() -> None:
    rows = "\n".join(f"| Item {n} | AUD {n}.00 | 2025-0{n % 9 + 1} | 0 up |" for n in range(40))
    table = "| Item | Price | Reported | Votes |\n|---|---|---|---|\n" + rows
    blocks = split_blocks(table, max_block_chars=300)
    assert len(blocks) > 2
    assert all(block.startswith("| Item | Price | Reported | Votes |") for block in blocks)
