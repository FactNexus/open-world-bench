# Evaluation design

## Why there is no single gold answer

Open-world tourism questions can have several acceptable answers, and the supporting web changes. Evaluation therefore asks whether the response is useful, constraint-compliant, and supported by appropriate evidence.

## Evaluation pipeline

1. Validate that an answer exists and can be parsed.
2. Extract recommendations, material factual claims, qualifications, and citations.
3. Run deterministic checks.
4. Build a shared scenario evidence bundle from all candidate citations plus bounded independent corroboration.
5. Evaluate claim support and contradiction against the frozen bundle.
6. Evaluate source suitability and freshness.
7. Evaluate scenario-specific rubric criteria.
8. Apply hard-constraint caps.
9. Store criterion explanations and confidence.
10. Aggregate quality dimensions and keep efficiency separate.

## Default dimensions

| Dimension | Default weight |
|---|---:|
| Constraint satisfaction | 25% |
| Citation support and grounded factuality | 25% |
| Factual correctness | 20% |
| Coverage and practical usefulness | 15% |
| Source suitability and freshness | 10% |
| Clarity and uncertainty handling | 5% |

## Useful evidence metrics

- **Citation precision:** proportion of citations that support at least one linked material claim.
- **Citation coverage:** proportion of externally verifiable material claims with adequate support.
- **Unsupported claim rate:** proportion of material factual claims lacking support.
- **Contradiction rate:** proportion of claims contradicted by cited evidence.
- **Freshness adequacy:** proportion of time-sensitive claims supported by current-enough evidence.
- **Source suitability:** whether the source type is appropriate for the claim type.

## Identity blinding

Candidate identity must be removed from judge prompts. For pairwise evaluations, randomise answer ordering and store the randomisation key separately.

## Human review triggers

Queue a run for review when:

- judges disagree beyond a threshold;
- a hard constraint is uncertain;
- most citations are blocked or paywalled;
- authoritative sources conflict;
- the answer depends on safety-critical or accessibility claims;
- the evaluator reports low confidence.

## Efficiency

Do not penalise quality directly for extra searches or tokens. Show cost and latency next to quality and calculate Pareto-efficient systems. This lets a curated ontology demonstrate value even where final answer quality is similar.


## Shared evidence bundle

Build the bundle only after every candidate has completed the scenario. Evaluate all answers against the same frozen set of retrieved pages. Preserve which sources were cited by each candidate and which were added by the evaluator. Independent corroboration should target critical and disputed claims, not attempt exhaustive domain crawling.
