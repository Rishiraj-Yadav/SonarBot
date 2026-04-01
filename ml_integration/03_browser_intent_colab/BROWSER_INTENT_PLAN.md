# Phase 3: Browser Intent Classifier (Colab + ONNX)

## Strategy

- Local: collect labeled/weakly-labeled intent data.
- Colab: fine-tune model with GPU.
- Export: quantized ONNX for local CPU inference.

## Tasks

- [ ] Add `assistant/ml/data_collection/intent_logger.py`.
- [ ] Add safe redaction for PII before export.
- [ ] Add `colab_notebooks/browser_intent_training.ipynb`.
- [ ] Export ONNX + quantized ONNX.
- [ ] Add `assistant/ml/inference/intent_predictor.py`.
- [ ] Integrate into `assistant/browser_workflows/nlp.py` in hybrid mode.

## Hybrid Rollout

- ML predicts intent + confidence.
- If confidence below threshold -> regex engine fallback.
- Log disagreement cases for retraining.

## Acceptance

- [ ] Inference under CPU latency target.
- [ ] No major regression on existing browser commands.
