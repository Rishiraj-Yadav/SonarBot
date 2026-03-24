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

Static cron jobs live here in config. You can also create user-specific dynamic cron jobs from chat with:

- `/cron add "0 8 * * *" "Good morning briefing"`
- `/cron list`
- `/cron pause <cron_id>`
- `/cron resume <cron_id>`
- `/cron delete <cron_id>`

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
- `browser_profiles_subdir`: path-like string, default `browser_sessions`
- `browser_screenshots_subdir`: path-like string, default `browser`
- `browser_downloads_subdir`: path-like string, default `inbox/browser_downloads`
- `browser_log_retention`: integer, default `200`
- `browser_screenshot_stream_interval_seconds`: integer, default `3`

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

### `[system_access]`

- `enabled`: boolean, default `false`; enables host-system file and shell access
- `home_root`: path, default `~`; legacy fallback root used when no `path_rules` are configured
- `shell`: string, default `powershell`
- `approval_timeout_seconds`: integer, default `300`
- `ask_once_session_cache`: boolean, default `true`
- `default_outside_policy`: string, default `deny`; what happens when a path does not match any allowed rule
- `protected_roots`: list of paths that are always blocked, even if a broader rule would otherwise allow them
- `audit_log_path`: path, default `~/.assistant/logs/system_actions.jsonl`
- `backup_root`: path, default `~/.assistant/backups/system_access`

### `[[system_access.path_rules]]`

- `path`: path root that the rule applies to
- `read`: `auto_allow | ask_once | always_ask | deny`
- `write`: `auto_allow | ask_once | always_ask | deny`
- `overwrite`: `auto_allow | ask_once | always_ask | deny`
- `delete`: `auto_allow | ask_once | always_ask | deny`
- `execute`: `auto_allow | ask_once | always_ask | deny`

Default Windows policy in the example:

- limited `C:` access for `~/Desktop`, `~/Documents`, `~/Downloads`, `~/Pictures`, `~/Music`, and `~/Videos`
- broad `R:/` access
- protected/system paths always denied
- reads/searches/listing allowed inside those roots
- create/write/copy/move/execute ask once per session
- overwrite/delete always ask

### `[users]`

- `default_user_id`: string, default `default`
- `primary_channel`: string, default `webchat`
- `fallback_channels`: list of strings
- `quiet_hours_start`: string in `HH:MM` format
- `quiet_hours_end`: string in `HH:MM` format
- `notification_level`: string, default `normal`
- `automation_enabled`: boolean, default `true`
- `auto_link_single_user`: boolean, default `true`
