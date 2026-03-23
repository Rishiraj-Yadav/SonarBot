# Config Reference

## `config.toml.example`

### `[gateway]`

- `host`: string, default `127.0.0.1`
- `port`: integer, default `8765`
- `token`: string, required
- `rate_limit_per_minute`: integer, default `10`

### `[agent]`

- `workspace_dir`: path, required
- `model`: string, default `gemini-2.0-flash`
- `max_tokens`: integer, default `2048`
- `context_window`: integer, default `32768`
- `max_sessions_per_key`: integer, default `20`
- `session_max_age_days`: integer, default `90`

### `[agent.compaction.memory_flush]`

- `enabled`: boolean, default `true` in the example

### `[llm]`

- `gemini_api_key`: string, usually sourced from `.env`
- `openai_api_key`: string, optional

### `[channels]`

- `enabled`: list of strings, for example `["telegram"]`

### `[telegram]`

- `bot_token`: string, optional
- `allowed_user_ids`: list of integers

### `[memory]`

- `vector_enabled`: boolean, default `true`
- `temporal_decay_lambda`: float, default `0.02`
- `mmr_lambda`: float, default `0.7`
- `multimodal_enabled`: boolean, default `true`
- `auto_capture_enabled`: boolean, default `true`; automatically promotes stable user facts and preferences into `MEMORY.md`

### `[automation]`

- `heartbeat_interval_minutes`: integer, default `15`
- `cron_jobs`: list of `{ schedule, message }`
- `rules`: list of structured automation rules

### `[automation.delivery]`

- `retry_attempts`: integer, default `3`
- `retry_backoff_seconds`: integer, default `30`
- `fallback_to_secondary`: boolean, default `true`

### `[automation.approvals]`

- `enabled`: boolean, default `true`
- `timeout_minutes`: integer, default `60`
- `default_action`: string, default `deny`

### `[automation.notifications]`

- `inbox_retention_days`: integer, default `30`
- `default_severity`: string, default `info`

### `[[automation.rules]]`

- `name`: string
- `trigger`: string, for example `cron`, `heartbeat`, or `webhook:github_push`
- `prompt_or_skill`: string
- `enabled`: boolean
- `action_policy`: string, default `notify_first`
- `delivery_policy`: string, default `primary`
- `cooldown_seconds`: integer
- `dedupe_window_seconds`: integer
- `quiet_hours_behavior`: string
- `severity`: string

Example:

```toml
cron_jobs = [{ schedule = "0 8 * * *", message = "Good morning briefing" }]
```

### `[tools]`

- `brave_api_key`: string, optional
- `browser_headless`: boolean, default `true`

### `[oauth.google]`

- `client_id`: string
- `client_secret`: string

### `[oauth.github]`

- `client_id`: string
- `client_secret`: string

### `[sandbox]`

- `enabled`: boolean, default `false`
- `image`: string, default `python:3.12-slim`
- `cpu_limit`: float, default `0.5`
- `memory_limit_mb`: integer, default `512`

### `[users]`

- `default_user_id`: string, default `default`
- `primary_channel`: string, default `webchat`
- `fallback_channels`: list of strings
- `quiet_hours_start`: string in `HH:MM` format
- `quiet_hours_end`: string in `HH:MM` format
- `notification_level`: string, default `normal`
- `automation_enabled`: boolean, default `true`
- `auto_link_single_user`: boolean, default `true`
