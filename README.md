# SonarBot

SonarBot is a local-first autonomous AI assistant that runs as a daemon, persists its memory and sessions on disk, serves multiple user channels, and now includes Phase 4 capabilities for OAuth, sub-agents, sandboxed execution, and runtime hardening.

## Current Project Status

- [x] Phase 1 foundation: FastAPI gateway, WebSocket protocol, CLI REPL, Gemini provider, core file/shell tools, JSONL session storage
- [x] Phase 2 memory + Telegram: daily and long-term markdown memory, hybrid search, Telegram adapter, session pruning, pre-compaction memory flush
- [x] Phase 3 webchat + automation: WebChat UI/backend, skills, hooks, cron, heartbeat, standing orders, webhook ingestion, browser/PDF/search tools
- [x] Phase 4 OAuth: encrypted OAuth token storage, local callback flow manager, Google + GitHub provider support, `oauth_connect` and `oauth_status` tools
- [x] Phase 4 multi-agent: presence registry, sub-agent manager, isolated delegated sessions, `agent_send` tool
- [x] Phase 4 sandboxing: Docker-backed sandbox runtime plus sandbox-aware `exec_shell`
- [x] Phase 4 hardening: request rate limiting, structured logging setup, device registry, richer health payload, graceful shutdown waiting, retry + circuit breaker for model calls
- [x] Phase 4 CLI updates: `devices` and `sessions` command groups, service-file generation during onboarding
- [x] Automated test coverage across Phases 1-4
- [ ] Phase 5 advanced memory ranking, browser login sessions, ACP interop, deployment docs, and full production polish

## What The Repo Includes Now

- CLI over `WS /ws`
- Telegram channel adapter
- WebChat UI over `WS /webchat/ws`
- markdown memory plus optional vector search
- slash commands, skills, hooks, cron, heartbeat, and webhooks
- OAuth token management for Google and GitHub
- sub-agent delegation with isolated sessions
- optional Docker sandbox execution

## Quickstart

1. Install Python dependencies.

```bash
uv sync --extra dev
```

2. If you want the optional semantic memory stack too:

```bash
uv sync --extra dev --extra memory
```

3. Copy `.env.example` to `.env` and add at least `GEMINI_API_KEY`.

```bash
copy .env.example .env
```

4. Run onboarding.

```bash
uv run assistant onboard
```

5. Start the gateway.

```bash
uv run assistant start
```

6. In another terminal, check health and chat.

```bash
uv run assistant status
uv run assistant chat
```

7. Optional WebChat:

```bash
cd webchat
npm install
npm run dev
```

Then open `http://localhost:3000`.

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

## CLI Commands

Core:

- `assistant start`
- `assistant status`
- `assistant chat`
- `assistant onboard`

Phase 4 additions:

- `assistant devices list`
- `assistant devices approve <id>`
- `assistant devices revoke <id>`
- `assistant sessions list`
- `assistant sessions view <id>`
- `assistant sessions export <id>`

## Project Layout

```text
assistant/   Runtime: gateway, agent loop, channels, memory, oauth, multi-agent, sandbox, tools
cli/         Typer CLI, onboarding, ws client, device/session command groups
tests/       Unit and integration coverage
webchat/     Next.js 15 + Tailwind control plane
workspace/   Default workspace prompt and memory templates
```

## Still Open

- ACP interoperability
- advanced memory re-ranking and browser login session reuse
- deeper production deployment polish and documentation
