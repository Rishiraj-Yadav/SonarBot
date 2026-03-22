# Architecture

## Overview

SonarBot is built around a single gateway process that accepts messages from multiple channels, routes them through one shared agent runtime, persists state on disk, and streams results back to the originating client.

Flow:

1. Client/channel sends a message to the gateway.
2. The gateway authenticates, validates, rate-limits, and routes the request.
3. The agent loop loads session state, system prompt context, memory, and skills.
4. The model responds with text and optional tool calls.
5. Tools execute and feed results back into the same turn.
6. The final response is streamed back to CLI, Telegram, or WebChat.
7. Sessions, memory, OAuth tokens, logs, and device state are stored locally.

## Core Layers

### Gateway

- FastAPI server with `/ws`, `/webchat/ws`, `/__health`, webhook routes, and REST control-plane endpoints
- connection manager with device tracking, channel route mapping, and rate limiting
- router for slash commands, inline command responses, hooks, and agent queueing

### Agent Runtime

- queue-driven `AgentLoop`
- session manager with JSONL persistence, pruning, and snapshots
- compaction manager with pre-compaction memory flush
- dynamic system prompt builder combining workspace files, memory, and skills

### Memory

- long-term memory in `workspace/MEMORY.md`
- daily logs in `workspace/memory/YYYY-MM-DD.md`
- hybrid BM25 + vector retrieval
- temporal decay scoring
- MMR reranking for diversity
- multimodal indexing for notes with attached image paths

### Channels

- CLI websocket client
- Telegram adapter with allowlisting and streamed edits
- WebChat browser client using the same request/event protocol

### Tools

- file read/write
- host or sandboxed shell exec
- browser automation + browser login sessions
- PDF extraction
- web search
- memory tools
- OAuth tools
- sub-agent delegation
- ACP external-agent dispatch

### Automation

- hooks
- cron jobs
- heartbeat turns
- standing orders
- inbound signed webhooks

### Integrations

- Gemini model provider
- Google and GitHub OAuth
- Docker sandbox
- ACP-compatible local agents

## Storage Model

- sessions: `~/.assistant/sessions/`
- logs: `~/.assistant/logs/`
- OAuth tokens: `~/.assistant/oauth/`
- device registry + structured state: `~/.assistant/assistant.db`
- vector store: `~/.assistant/chroma/`
- workspace-owned prompts/memory/skills/hooks under `workspace/`
