# Fairness and reproducibility

## Paired execution

The same scenario instance must be used for every system. Randomise run order and keep the time gap small, especially for time-sensitive tasks.

## Configuration disclosure

Record model identifier, provider, search mode, reasoning setting, temperature, system prompt additions, timeout, and date. Results without this metadata should be marked incomplete.

## Repetitions

Use at least three repetitions for stochastic systems when budget permits. Compare systems by paired scenario differences rather than only aggregate means.

## Telemetry asymmetry

Hosted products may expose fewer metrics than custom systems. Do not treat absent telemetry as zero. Use `null` and exclude unavailable metrics from derived efficiency comparisons.

## Candidate-specific prompts

Provider-specific formatting may be necessary, but semantic instructions must remain equivalent. Store the final prompt sent to each system and report any adapter additions.

## Web drift

Store evidence hashes and timestamps. A result means “performance under the recorded web and system conditions,” not permanent truth.
