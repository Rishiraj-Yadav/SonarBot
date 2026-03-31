# Config Reference

## `config.toml.example`

### `[gateway]`

- `host`: string, default `127.0.0.1`
- `port`: integer, default `8765`
- `token`: string, required
- `rate_limit_per_minute`: integer, default `10`

### `[agent]`

- `workspace_dir`: path, required
- `model`: string, default `gemini-2.5-pro`
- `max_tokens`: integer, default `2048`
- `context_window`: integer, default `32768`
- `max_sessions_per_key`: integer, default `20`
- `session_max_age_days`: integer, default `90`

### `[agent.compaction.memory_flush]`

- `enabled`: boolean, default `true` in the example

### `[llm]`

- `gemini_api_key`: string, usually sourced from `.env`
- `anthropic_api_key`: string, optional; enables Anthropic-backed cheap/background LLM routing when configured
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
- `cron_jobs`: list of `{ schedule, message, mode }`
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

### `[context_engine]`

- `enabled`: boolean, default `false`; turns on the proactive life-context engine
- `interval_minutes`: integer, default `180`; how often the background engine runs
- `recent_session_message_limit`: integer, default `6`; max messages loaded from a recent session
- `session_count_limit`: integer, default `4`; max recent sessions considered per user
- `gmail_thread_limit`: integer, default `5`; max recent Gmail threads inspected when Google is connected
- `calendar_event_limit`: integer, default `6`; max upcoming calendar events inspected when Google is connected
- `max_notifications_per_run`: integer, default `2`; delivery cap for proactive insights per run
- `min_confidence`: float, default `0.82`; minimum model confidence required to notify
- `min_urgency`: float, default `0.55`; minimum urgency required to notify
- `dedupe_days`: integer, default `7`; suppress repeated insights within this time window
- `snapshot_subdir`: path-like string, default `context_engine/life_state`
- `insights_subdir`: path-like string, default `context_engine/insights`

This subsystem is read-mostly. It builds a separate life-state snapshot instead of writing into `MEMORY.md`, and it delivers only high-confidence insights through the existing notification system while respecting quiet hours.

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

`mode` may be:
- `direct`: deliver the reminder/notification immediately without routing through the model
- `ai`: run the cron job through the automation reasoning flow, which may summarize, decide to notify, or return `NO_REPLY`

```toml
cron_jobs = [{ schedule = "0 8 * * *", message = "Good morning briefing", mode = "direct" }]
```

### `[tools]`

- `brave_api_key`: string, optional
- `browser_headless`: boolean, default `true`
- `browser_profiles_subdir`: path-like string, default `browser_sessions`
- `browser_screenshots_subdir`: path-like string, default `browser`
- `browser_downloads_subdir`: path-like string, default `inbox/browser_downloads`
- `browser_log_retention`: integer, default `200`
- `browser_screenshot_stream_interval_seconds`: integer, default `3`

### `[browser_workflows]`

- `enabled`: boolean, default `true`; turns on the autonomous browser workflow layer
- `classifier_confidence_threshold`: float, default `0.82`; minimum confidence required for the LLM classifier fallback to auto-run
- `max_results_to_rank`: integer, default `8`; maximum visible search results ranked for Google/YouTube/site-search workflows
- `allow_auto_play`: boolean, default `true`; allows low-risk media-play attempts after opening a YouTube watch page
- `ask_before_high_impact`: boolean, default `true`; reserves account-changing browser actions for an explicit confirmation step
- `llm_classifier_enabled`: boolean, default `true`; enables the lightweight classifier fallback when deterministic browser matching is uncertain

This layer sits above the existing Playwright tools. It supports hybrid matching for natural-language browser tasks such as:

- `open youtube and play Trapped On An Island Until I Build A Boat`
- `search google for SonarBot GitHub and open the first result`
- `open leetcode and search arrays problems`

You can also inspect and force those workflows through slash commands:

- `/browser workflows`
- `/browser task <instruction>`

### `[browser_execution]`

- `default_mode`: `headless | headed`, default `headless`; the preferred browser mode for low-risk automation
- `headed_login_required`: boolean, default `true`; open a visible browser window for manual login flows
- `headed_on_blockers`: boolean, default `true`; switch blocked headless tasks into a visible intervention window for captcha, consent, and security pages
- `headed_on_high_impact`: boolean, default `true`; reserve irreversible browser actions for visible review/confirmation
- `revert_to_headless_after_manual_step`: boolean, default `true`; after login or review, return normal work to headless mode
- `keep_headed_browser_alive_seconds`: integer, default `60`; how long to keep the visible browser open after manual intervention before closing it automatically
- `human_simulation`: boolean, default `false`; enables slower, more human-like mouse movement, typing, scroll, and viewport variation for bot-sensitive sites

This gives SonarBot a hybrid browser policy:

- background search, read, extract, and compare tasks run headlessly
- login, blocker handling, and protected review steps use a visible browser window
- users can still override per task with natural-language hints like `show me what you're doing` or `run silently`

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

- `enabled`: boolean, default `false` in code; `config.toml.example` sets `true`. Environment override: `SYSTEM_ACCESS_ENABLED` (`true` / `false`). When enabled, the agent may use host file tools and `exec_shell` with `host=true` subject to path rules and approvals.
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
- `primary_channel`: string, default `webchat`; supported built-ins include `webchat`, `telegram`, `cli`, and `windows` for native Windows toast notifications
- `fallback_channels`: list of strings using the same channel names as `primary_channel`
- `quiet_hours_start`: string in `HH:MM` format
- `quiet_hours_end`: string in `HH:MM` format
- `notification_level`: string, default `normal`
- `automation_enabled`: boolean, default `true`
- `auto_link_single_user`: boolean, default `true`
