# Phase 1: Local Tool Router (Token Saver)

## Model Choice

CPU-first baseline: `TF-IDF + LogisticRegression` (multi-label).

## Integration Path

- Train offline from logged `(message -> tools_used)` pairs.
- At runtime, predict candidate tools from latest user message.
- Always include a safety set:
  - `llm_task`
  - core file tools
  - core browser tools
- If confidence low: fallback to full schema set.

## Tasks

- [ ] Add `assistant/ml/tool_router.py`.
- [ ] Add `assistant/ml/models/` local model loading utility.
- [ ] Add runtime selector in `assistant/agent/loop.py` before `get_tools_schema()`.
- [ ] Add metrics: `tools_total`, `tools_selected`, `selection_confidence`.
- [ ] Add shadow mode flag in config (`enabled`, `shadow_mode`, `min_confidence`).

## Acceptance

- [ ] No tool-availability regressions in tests.
- [ ] Tool schema payload reduced significantly on normal chat turns.
