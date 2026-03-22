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

### `[automation]`

- `heartbeat_interval_minutes`: integer, default `15`
- `cron_jobs`: list of `{ schedule, message }`

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
