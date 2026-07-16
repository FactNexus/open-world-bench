"""Framework deterministic checks (SPEC.md 16.3).

These run for every evaluation, need no domain code, and never call a judge:
answer presence, citation presence/parseability, requested item count, and
duplicate recommendations. They emit :class:`CriterionResult` findings with
``framework.``-prefixed criterion IDs so they never collide with template
criteria.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from owrb.models import CriterionResult, RunResult, ScenarioInstance

_NUMBERED_ITEM = re.compile(r"^\s{0,3}\d+[.)]\s+(.+)$", re.MULTILINE)
_BULLET_ITEM = re.compile(r"^\s{0,3}[-*]\s+(.+)$", re.MULTILINE)
_HEADING_ITEM = re.compile(r"^#{2,4}\s+(.+)$", re.MULTILINE)
_MIN_ANSWER_LENGTH = 40


def extract_recommendation_titles(answer: str) -> list[str]:
    """Best-effort list of recommended items from markdown structure."""
    for pattern in (_NUMBERED_ITEM, _HEADING_ITEM, _BULLET_ITEM):
        matches = pattern.findall(answer)
        if len(matches) >= 2:
            titles = []
            for match in matches:
                title = re.sub(r"[*_`#]", "", match).strip()
                title = title.split(":")[0].split(" — ")[0].split(" - ")[0].strip()
                if title:
                    titles.append(title)
            return titles
    return []


def _is_parseable_http_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def run_deterministic_checks(
    scenario: ScenarioInstance, result: RunResult
) -> list[CriterionResult]:
    findings: list[CriterionResult] = []
    answer = result.answer.strip()

    has_answer = len(answer) >= _MIN_ANSWER_LENGTH
    findings.append(
        CriterionResult(
            criterion_id="framework.answer-presence",
            dimension="coverage",
            score=1.0 if has_answer else 0.0,
            passed=has_answer,
            explanation=(
                f"answer has {len(answer)} characters"
                if has_answer
                else f"answer is empty or too short ({len(answer)} characters)"
            ),
            confidence=1.0,
        )
    )

    if scenario.answer_contract.citations_required:
        has_citations = bool(result.citations)
        findings.append(
            CriterionResult(
                criterion_id="framework.citations-present",
                dimension="citation_support",
                score=1.0 if has_citations else 0.0,
                passed=has_citations,
                explanation=(
                    f"{len(result.citations)} citations supplied"
                    if has_citations
                    else "citations were required but none were supplied"
                ),
                confidence=1.0,
            )
        )

    if result.citations:
        parseable = [
            citation for citation in result.citations if _is_parseable_http_url(citation.url)
        ]
        share = len(parseable) / len(result.citations)
        findings.append(
            CriterionResult(
                criterion_id="framework.citations-parseable",
                dimension="citation_support",
                score=share,
                passed=share == 1.0,
                explanation=(
                    f"{len(parseable)}/{len(result.citations)} citations are "
                    "parseable http(s) URLs"
                ),
                confidence=1.0,
            )
        )

    requested_count = scenario.parameters.get("recommendation_count")
    titles = extract_recommendation_titles(result.answer)
    if isinstance(requested_count, int) and requested_count > 0:
        if titles:
            matches = len(titles) == requested_count
            findings.append(
                CriterionResult(
                    criterion_id="framework.requested-count",
                    dimension="constraint_satisfaction",
                    score=1.0 if matches else 0.0,
                    passed=matches,
                    explanation=(
                        f"found {len(titles)} recommendations; {requested_count} requested"
                    ),
                    # Markdown structure counting is a heuristic; the rubric
                    # judge re-checks this criterion when configured.
                    confidence=0.7,
                )
            )
        else:
            findings.append(
                CriterionResult(
                    criterion_id="framework.requested-count",
                    dimension="constraint_satisfaction",
                    score=0.5,
                    passed=None,
                    explanation=(
                        "could not identify a recommendation list structure to count"
                    ),
                    confidence=0.2,
                )
            )

    if len(titles) >= 2:
        normalised = [title.casefold() for title in titles]
        unique = len(set(normalised))
        share = unique / len(normalised)
        findings.append(
            CriterionResult(
                criterion_id="framework.no-duplicate-recommendations",
                dimension="coverage",
                score=share,
                passed=share == 1.0,
                explanation=f"{unique}/{len(normalised)} recommendation titles are unique",
                confidence=0.9,
            )
        )

    return findings
