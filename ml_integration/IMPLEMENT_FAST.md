# Fast Execution Order

1. Phase 0 baseline telemetry
2. Phase 1 tool router (highest ROI)
3. Phase 2 memory classifier
4. Phase 3 browser intent hybrid (ML + regex fallback)
5. Phase 4 dashboard

## Delivery Style

- Ship each phase behind config flags.
- Start in shadow mode.
- Compare metrics for 2-3 days.
- Then enable active mode.
