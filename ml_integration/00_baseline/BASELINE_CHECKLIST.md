# Phase 0: Baseline + Telemetry

## Objective

Capture current behavior before ML changes.

## Must-Track Metrics

- End-to-end response latency (`p50`, `p95`)
- Tool schema payload size per turn
- Estimated input tokens per turn
- Browser workflow trigger precision/recall sample
- Memory auto-capture keep/drop quality sample

## Fast Implementation Tasks

- [ ] Add backend counters for tool schemas attached per turn.
- [ ] Add backend counters for tools selected per turn.
- [ ] Add latency timers around model completion.
- [ ] Add daily JSONL metric dump to `workspace/metrics/`.
- [ ] Add one script to summarize the last 7 days.

## Acceptance

- [ ] We can compare before/after token + latency.
- [ ] No behavior change to production routing yet.
