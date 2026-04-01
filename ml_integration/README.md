# ML Integration Workspace

This folder is a dedicated workspace for the SonarBot ML integration rollout.

Goal: ship fast with low-risk, phase-wise adoption.

## Structure

- `00_baseline/`
  - Metrics + telemetry before any ML switch.
- `01_tool_router/`
  - Local CPU-friendly tool routing model.
- `02_memory_classifier/`
  - Local memory keep/drop model.
- `03_browser_intent_colab/`
  - Colab training pipeline + ONNX local inference.
- `04_dashboard/`
  - Status API + frontend dashboard.

## Rollout Rules

1. Keep deterministic fallback paths active.
2. Introduce models in shadow mode first.
3. Use confidence thresholds and abstain/fallback behavior.
4. Prefer high recall for routing tasks.
5. Never break current user flows while experimenting.
