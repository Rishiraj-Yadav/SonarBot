# Phase 4: Model Status Dashboard

## Backend

Add `/api/ml/status` endpoint with:

- model enabled/disabled state
- model version/hash
- moving average latency
- confidence and abstain rates
- token savings metrics

## Frontend

Add `ModelStatusDashboard` component with:

- health badges (green/yellow/red)
- token savings chart (7-day)
- dataset backlog progress

## Tasks

- [ ] Add status service in backend.
- [ ] Add endpoint in gateway server.
- [ ] Add webchat component.
- [ ] Add polling + rendering.
- [ ] Add empty/error states.

## Acceptance

- [ ] Status page shows live metrics without impacting response path.
