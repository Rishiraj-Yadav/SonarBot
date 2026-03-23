# SonarBot

SonarBot is a local-first autonomous AI assistant that runs as a daemon, keeps its state on disk, serves CLI, Telegram, and WebChat clients, and now includes the full Phase 6 baseline: advanced memory, browser login session reuse, ACP interop, sandbox execution, diagnostics, and OAuth-backed Gmail/GitHub integrations.

## Current Project Status

- [x] Phase 1 foundation: FastAPI gateway, WebSocket protocol, CLI REPL, Gemini provider, core file/shell tools, JSONL session storage
- [x] Phase 2 memory + Telegram: daily and long-term markdown memory, hybrid search, Telegram adapter, session pruning, pre-compaction memory flush
- [x] Phase 3 webchat + automation: WebChat UI/backend, skills, hooks, cron, heartbeat, standing orders, webhook ingestion, browser/PDF/search tools
- [x] Phase 4 OAuth + orchestration: encrypted OAuth storage, Google/GitHub flows, sub-agents, sandbox-aware shell execution, device/session CLI commands, structured logging, graceful shutdown
- [x] Phase 5 advanced polish: temporal memory decay, MMR reranking, multimodal memory indexing, browser login sessions, session snapshots, ACP client/tool, `assistant doctor`, expanded e2e/load/unit tests, deployment/config docs
- [x] Phase 6 service integrations: Gmail search/read/send/draft tools, GitHub repo/issue/PR read tools, Gmail briefing skill, GitHub PR summary skill, OAuth token fallback for connected accounts
- [x] Automation V2: unified user profiles, persisted automation runs, background cron/heartbeat/webhook execution, notification inbox, primary-channel delivery, rule pause/resume controls

## What You Can Use Today

- CLI chat over `WS /ws`
- Telegram bot replies with streaming edits
- WebChat UI over `WS /webchat/ws`
- persistent sessions with compaction and snapshots
- markdown memory with hybrid search, temporal decay, MMR, and memory stats
- browser, PDF, web search, shell, file, OAuth, ACP, and sub-agent tools
- host-system file access with policy-based drive and folder rules
- Gmail tools: search, read thread, send, create draft
- GitHub tools: list repos, list issues, list pull requests, get pull request details
- hooks, cron jobs, heartbeat turns, standing orders, and signed webhooks
- automation inbox and run history in WebChat
- optional Docker sandbox execution

## Quickstart In 5 Steps

1. Install dependencies.

```bash
uv sync --extra dev
```

2. If you want semantic/vector memory too:

```bash
uv sync --extra dev --extra memory
```

3. Copy `.env.example` to `.env` and set at least `GEMINI_API_KEY`.

```bash
copy .env.example .env
```

4. Run onboarding.

```bash
uv run assistant onboard
```

5. Start the gateway and try the clients.

```bash
uv run assistant start
uv run assistant status
uv run assistant chat
```

Optional WebChat:

```bash
cd webchat
npm install
npm run dev
```

Then open `http://localhost:3000`.

## Screenshots

- WebChat UI: run the Next.js frontend and open `http://localhost:3000`
- Telegram conversation: message the configured bot from an allowlisted Telegram account

The repo doesn’t currently commit screenshot image assets, but the UI and Telegram flows are live and ready to capture locally.

## API Keys And Secrets

Required:

- `GEMINI_API_KEY`

Optional:

- `TELEGRAM_BOT_TOKEN`
- `OPENAI_API_KEY`
- `BRAVE_API_KEY`
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`
- `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET`

Recommended:

- keep secrets in local untracked `.env`
- keep non-secret runtime config in `~/.assistant/config.toml`

## Host Access Policy

SonarBot now supports policy-based host access instead of a single home-root rule.

Default Windows policy:

- allowed `C:` folders:
  - `~/Desktop`
  - `~/Documents`
  - `~/Downloads`
  - `~/Pictures`
  - `~/Music`
  - `~/Videos`
- broadly allowed data drive:
  - `R:/`
- always denied protected paths:
  - `C:/Windows`
  - `C:/Program Files`
  - `C:/Program Files (x86)`
  - `C:/ProgramData`
  - `~/AppData`
  - `C:/$Recycle.Bin`
  - `C:/System Volume Information`
  - `R:/$Recycle.Bin`
  - `R:/System Volume Information`

Approval model:

- read/list/search in allowed roots: auto-allow
- create/write/copy/move/execute in allowed roots: ask once per session
- overwrite/delete: always ask
- destructive/system commands: always deny

You can customize this in `~/.assistant/config.toml` with `[system_access]`, `protected_roots`, and `[[system_access.path_rules]]`.

## CLI Commands

Core:

- `assistant start`
- `assistant status`
- `assistant chat`
- `assistant onboard`
- `assistant doctor`

Management:

- `assistant devices list`
- `assistant devices approve <id>`
- `assistant devices revoke <id>`
- `assistant sessions list`
- `assistant sessions view <id>`
- `assistant sessions export <id>`

## Project Layout

```text
assistant/   Runtime: gateway, agent loop, channels, memory, oauth, multi-agent, sandbox, tools
cli/         Typer CLI, onboarding wizard, diagnostics, ws client, device/session commands
docs/        Architecture, skills, hooks, deployment, and config reference
tests/       Unit, integration, e2e, and load coverage
webchat/     Next.js 15 + Tailwind control plane
workspace/   Default workspace prompt, memory, automation, and identity templates
```

## Documentation

- `docs/architecture.md`
- `docs/skills.md`
- `docs/hooks.md`
- `docs/deployment.md`
- `docs/config_reference.md`
