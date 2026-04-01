# Phase 2: Local Memory Importance Classifier

## Model Choice

Start simple: `TF-IDF + LogisticRegression` (binary keep/drop).
Upgrade to embeddings later only if needed.

## Integration Path

- Replace rigid heuristics in auto-capture with classifier gate.
- Preserve old heuristics as fallback when classifier abstains.

## Tasks

- [ ] Add `assistant/ml/memory_classifier.py`.
- [ ] Add training script from existing memory logs.
- [ ] Add confidence threshold + abstain behavior.
- [ ] Add false-positive/false-negative sampling report.
- [ ] Add config flags (`enabled`, `min_confidence`, `shadow_mode`).

## Acceptance

- [ ] Fewer noisy memories captured.
- [ ] Important user preferences still captured.
