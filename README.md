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
- [x] Browser Automation V2: named browser profiles, multi-tab Playwright runtime, downloads/log capture, table extraction, form fill helpers, and a live browser panel in WebChat
- [x] App/Window Control V1: optional Windows-only app launch, window listing, focus/minimize/maximize/restore, and left/right snap commands
- [x] Desktop Vision V1: optional Windows-only active-window awareness, desktop/window screenshots, and OCR-backed screen reading
- [x] Desktop Input V1: optional Windows-only mouse position/move/click, keyboard typing/hotkeys, clipboard read/write, and approval-gated `/input` + `/clipboard` commands
- [x] Desktop Routines V1: optional structured routines that combine app control, host file actions, desktop vision, and desktop input into manual, scheduled, reminder, and file-watch workflows
- [x] App Skills V1: optional higher-level VS Code, document, Excel, system control, Task Manager, browser workspace, and study/work preset commands built on the current desktop primitives
- [x] Desktop Coworker V2: optional bounded `/coworker` planning with verified visual screen loops, transcripts, step-by-step verification, and safe skill-first fallbacks

## What You Can Use Today

- CLI chat over `WS /ws`
- Telegram bot replies with streaming edits
- WebChat UI over `WS /webchat/ws`
- persistent sessions with compaction and snapshots
- markdown memory with hybrid search, temporal decay, MMR, and memory stats
- browser, PDF, web search, shell, file, OAuth, ACP, and sub-agent tools
- advanced browser automation with named profiles, multi-tab control, uploads/downloads, logs, table extraction, and form autofill
- optional Windows app/window control with `/apps` commands and bounded natural-language shortcuts
- optional Windows desktop screenshots, active-window inspection, and OCR-backed `/screen` commands
- optional Windows desktop keyboard/mouse/clipboard control with `/input` and `/clipboard` commands
- optional Windows desktop routines with `/routine` commands plus bounded natural-language setup phrases such as `create a study mode that opens chrome and 6_semester folder`
- optional Windows app skills with `/vscode`, `/doc`, `/excel`, `/system`, `/task`, `/preset`, and `/browser-skill`
- optional Windows coworker tasks with `/coworker` commands for verified multi-step desktop work such as `open task manager and summarize system usage` or `open the file you see on screen now`
- host-system file access with policy-based drive and folder rules
- Gmail tools: search, read thread, send, create draft
- GitHub tools: list repos, list issues, list pull requests, get pull request details
- hooks, cron jobs, heartbeat turns, standing orders, and signed webhooks
- chat-managed cron jobs via `/cron add`, `/cron list`, `/cron pause`, `/cron resume`, and `/cron delete`
- automation inbox and run history in WebChat
- proactive life context engine with snapshotting, cross-source insight scoring, and quiet-hours-aware notifications
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

## Browser Automation V2

The browser subsystem now includes:

- named saved profiles per site/account
- multi-tab browsing tools
- smarter locator fallback and wait handling
- browser uploads and workspace-backed downloads
- console/network log capture
- table extraction and form autofill helpers
- WebChat browser panel with live state, tabs, downloads, logs, and headed-browser screenshot streaming

Browser downloads are stored under the workspace by default:

- `workspace/inbox/browser_downloads`

## Windows App Control V1

SonarBot now includes an optional Windows-only app/window control layer for safe desktop actions.

Phase 1 includes:

- launch configured app aliases such as Chrome, Edge, VS Code, Notepad, Explorer, Word, Excel, and WhatsApp
- list visible app windows
- focus, minimize, maximize, and restore windows
- snap a window to the left or right side of the monitor work area

Phase 1 does not include:

- close-window actions
- semantic keyboard or mouse automation
- clipboard workflows
- OCR or general desktop vision

Enable it in `~/.assistant/config.toml`:

```toml
[desktop_apps]
enabled = true
allow_layout_changes = true
launch_timeout_seconds = 8
known_apps = { chrome = "C:/Program Files/Google/Chrome/Application/chrome.exe", edge = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", vscode = "~/AppData/Local/Programs/Microsoft VS Code/Code.exe", notepad = "C:/Windows/System32/notepad.exe", explorer = "C:/Windows/explorer.exe", word = "C:/Program Files (x86)/Microsoft Office/root/Office16/WINWORD.EXE", excel = "C:/Program Files (x86)/Microsoft Office/root/Office16/EXCEL.EXE", outlook = "C:/Program Files (x86)/Microsoft Office/root/Office16/OUTLOOK.EXE", whatsapp = "~/AppData/Local/WhatsApp/WhatsApp.exe", taskmanager = "C:/Windows/System32/taskmgr.exe", settings = "C:/Windows/ImmersiveControlPanel/SystemSettings.exe", calculator = "C:/Windows/System32/calc.exe", cmd = "C:/Windows/System32/cmd.exe", powershell = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe" }
```

Example commands:

- `/apps list`
- `/apps open chrome`
- `/apps focus vscode`
- `/apps maximize word`
- `/apps left chrome`
- `open chrome`
- `switch to vscode`
- `maximize word`
- `put chrome on left`

This feature is Windows-only and disabled by default so existing installs keep their current behavior.

## Desktop Vision V1

SonarBot also includes an optional Windows-only desktop vision layer for safe read-only screen awareness.

Phase 2 includes:

- inspect the currently active window
- capture a full desktop screenshot into the workspace
- capture a screenshot of the active window
- OCR desktop screenshots or active-window captures
- combined screen-read flow through `/screen read`

Phase 2 does not include:

- clicks, typing, or hotkeys
- clipboard automation
- window close automation
- general desktop control beyond reading/capture

Enable it in `~/.assistant/config.toml`:

```toml
[desktop_vision]
enabled = true
ocr_enabled = true
screenshots_subdir = "desktop"
capture_format = "png"
max_ocr_characters = 12000
```

Example commands:

- `/screen active`
- `/screen capture`
- `/screen window`
- `/screen read`
- `/screen read window`
- `what app is active`
- `take a screenshot of my desktop`
- `capture the active window`
- `read the text on my screen`
- `read the active window`

Desktop Vision is Windows-only, stores captures inside the workspace, and remains disabled by default.

## Desktop Input V1

SonarBot also includes an optional Windows-only desktop input layer for safe manual keyboard, mouse, and clipboard control.

Phase 3 includes:

- get the current cursor position
- move the cursor to explicit coordinates
- left-click, right-click, and double-click at explicit coordinates
- scroll up or down by an explicit amount
- type text into the active window
- press explicit hotkeys such as `Ctrl+C`
- read and write clipboard text
- `copy selected text` as a combined `Ctrl+C` + clipboard-read flow

Phase 3 does not include:

- semantic clicking like `click the save button`
- OCR-targeted clicking
- drag and drop
- macros or multi-step replay
- automation-triggered input actions

Enable it in `~/.assistant/config.toml`:

```toml
[desktop_input]
enabled = true
keyboard_enabled = true
mouse_enabled = true
clipboard_enabled = true
allow_absolute_coordinates = true
max_type_chars = 500
confirm_clicks = true
confirm_typing = true
confirm_clipboard_write = true
confirm_risky_hotkeys = true
safe_hotkeys = ["ctrl+c", "ctrl+a", "ctrl+f", "tab", "shift+tab", "up", "down", "left", "right", "pageup", "pagedown", "home", "end", "esc"]
```

Example commands:

- `/input position`
- `/input move 400 300`
- `/input click 400 300`
- `/input right-click 400 300`
- `/input double-click 400 300`
- `/input scroll down 5`
- `/input type hello world`
- `/input hotkey ctrl+c`
- `/clipboard get`
- `/clipboard set hello`
- `move mouse to 400 300`
- `click at 400 300`
- `type hello world`
- `press ctrl c`
- `what is on my clipboard`
- `copy selected text`

Desktop Input is Windows-only, disabled by default, and reuses the existing `/host-approvals` flow for risky actions such as clicks, typing, clipboard writes, and non-allowlisted hotkeys.

## Desktop Routines V1

SonarBot also includes an optional Windows-only desktop routine layer for chaining the existing app, host-file, screen, and input primitives into bounded multi-step workflows.

Phase 4 includes:

- manual named routines such as `study mode`
- scheduled routines built from chat phrases like `every weekday at 9 am open chrome and vscode`
- reminder-style routines that notify and then open apps or folders
- file-watch routines that can move or copy matching files and notify you
- `/routine` commands to list, show, run, pause, resume, and delete desktop routines
- WebChat rule cards for running and managing saved routines

Phase 4 does not include:

- open-ended autonomous desktop loops
- OCR-targeted clicking
- drag-and-drop or macros
- app-specific deep Word/Excel/VS Code workflows
- automation-triggered freeform keyboard or mouse control outside structured routine steps

Enable desktop routines in `~/.assistant/config.toml` by turning on desktop automation:

```toml
[automation.desktop]
enabled = true
watch_enabled = true
```

`watch_enabled` is only required for file-watch triggers. Manual, scheduled, and reminder routines work with `watch_enabled = false`.

Example commands:

- `/routine list`
- `/routine show Study mode`
- `/routine run Study mode`
- `/routine pause Study mode`
- `/routine resume Study mode`
- `/routine delete Study mode`
- `create a study mode that opens chrome and 6_semester folder`
- `every weekday at 9 am open chrome and vscode`
- `remind me tomorrow at 8 pm to study and open 6_semester folder`
- `when a pdf file appears in download2, move it to documents and notify me`

Desktop Routines reuse the current approval system. Safe steps can auto-run, while risky steps like typing, clicking, overwriting files, or non-allowlisted hotkeys still require approval at execution time.

## App Skills V1

SonarBot also includes an optional Windows-only app-skills layer that builds on the existing host file access, app control, browser tools, desktop input, and desktop routine primitives instead of duplicating them.

Phase 5 includes:

- VS Code helpers for opening project folders or files and searching allowed host paths
- document helpers for reading, creating, and replacing text in `.txt`, `.md`, and `.docx`
- Excel helpers for creating simple `.xlsx` workbooks, appending rows, and previewing sheet data
- system helpers for opening Windows Settings pages plus reading/setting volume and brightness when supported
- Task Manager helpers that open Task Manager and return a brief CPU, memory, disk, and top-process summary
- built-in `study-mode`, `work-mode`, and `meeting-mode` presets
- a separate browser workspace adapter for opening configured study/work/meeting tab sets without modifying the core browser runtime path

Enable it in `~/.assistant/config.toml`:

```toml
[app_skills]
enabled = true
vscode_enabled = true
documents_enabled = true
excel_enabled = true
browser_enabled = true
system_enabled = true
task_manager_enabled = true
presets_enabled = true
browser_headed_for_workspaces = true

[app_skills.presets]
enabled = true
study_apps = ["explorer", "chrome"]
study_folder_hints = ["6_semester", "6 semester", "5_sem", "5 sem"]
study_browser_urls = []
work_apps = ["vscode", "chrome"]
work_folder_hints = ["workspace", "documents"]
work_browser_urls = []
meeting_apps = ["chrome"]
meeting_browser_urls = []
```

Example commands:

- `/vscode open 6_semester`
- `/vscode file R:/6_semester/mini_project/app.py`
- `/doc create R:/6_semester/notes.docx :: hello world`
- `/doc replace R:/6_semester/notes.docx :: hello :: hi`
- `/excel create R:/6_semester/marks.xlsx :: Name,Score`
- `/excel append-row R:/6_semester/marks.xlsx :: Ritesh,95`
- `/system volume`
- `/system volume set 40`
- `/system brightness`
- `/system settings bluetooth`
- `/task open`
- `/preset run study-mode`
- `/browser-skill open study`
- `open task manager`
- `open bluetooth settings`
- `open mini_project in vscode`
- `study mode`

App Skills V1 is disabled by default. Document and Excel operations continue to use the existing host approval model, and the browser workspace pack is intentionally isolated from the main browser runtime implementation path.

## Desktop Coworker V2

SonarBot now also includes an optional Windows-only coworker layer that plans and executes short verified desktop tasks on top of the existing app control, desktop vision, desktop input, routines, and app skills.

Phase 6B includes:

- persisted coworker task records and transcripts
- `/coworker plan`, `/coworker run`, `/coworker step`, `/coworker status`, `/coworker stop`, and `/coworker history`
- bounded task planning for supported multi-step requests such as:
  - open Task Manager and summarize system usage
  - open Bluetooth settings and report Bluetooth availability
  - open a project in VS Code and verify focus
  - update a document and verify the text change
  - copy selected text and summarize it
  - open the file you see on screen now
- step-by-step verification using active-window state, screen capture, OCR, clipboard state, or structured tool results
- safe reuse of Phase 5 app skills first, with lower-level desktop primitives only when needed
- screenshot-aware visual coworker loops that use screenshot -> LLM decision -> action -> screenshot verification for visible file and item flows

Phase 6B does not include:

- open-ended autonomous desktop control
- unrestricted image-only object detection
- drag-and-drop or macro recording
- submit/send/finalize actions without explicit approval
- unrestricted multi-step planning outside the supported bounded patterns

Enable it in `~/.assistant/config.toml`:

```toml
[desktop_coworker]
enabled = true
max_steps_per_task = 6
max_retries_per_step = 2
verification_required_by_default = true
ask_before_submission = true
screenshot_after_each_step = true
ocr_after_each_step = true
store_transcripts = true
visual_tasks_enabled = true
max_visual_steps = 8
max_target_candidates = 8
visual_target_confidence_threshold = 0.7
ask_on_low_confidence = true
allow_semantic_clicks = true
allow_ui_text_entry = true
default_visual_capture_target = "window"
stop_on_low_confidence = true
targeting_backend = "hybrid"
uia_enabled = true
ocr_boxes_enabled = true
keyboard_fallback_enabled = true
max_recovery_attempts = 3
max_visual_replans = 2
reopen_missing_apps = true
approval_preview_screenshots = true
artifact_retention_count = 20
```

Example commands:

- `/coworker plan open task manager and summarize system usage`
- `/coworker run open task manager and summarize system usage`
- `/coworker run open bluetooth settings and tell me whether bluetooth is available`
- `/coworker run open R:/6_semester/mini_project in vscode and confirm the window is focused`
- `/coworker run open the file you see on screen now`
- `/coworker status <task_id>`
- `/coworker history`
- `help me open task manager and summarize system usage`
- `help me copy selected text and summarize it`
- `open the visible hindi_english_parallel file`

WebChat now includes a dedicated `/coworker` panel for:

- active task state and current attempt
- screenshot timeline artifacts
- retry / continue / stop controls
- backend health for UIA, OCR boxes, and the legacy visual fallback
- pending host approval previews when a task blocks on input approval

Desktop Coworker V2 is disabled by default, remains intentionally bounded, and prefers deterministic Phase 5 skills before falling back to lower-level desktop actions. It does not watch the desktop as a live video stream; it runs a bounded screenshot-and-verification loop when a task depends on what is currently visible on screen.

## Proactive Life Context Engine

SonarBot now includes an optional background context engine that builds a separate life-state snapshot and only sends high-confidence proactive insights.

What it reads:

- markdown memory through the existing memory manager
- recent session history across linked channels
- Google Gmail and Calendar context when that account is connected
- automation notifications and automation run history

What it does:

- builds a snapshot under `workspace/context_engine/life_state`
- keeps dedupe history under `workspace/context_engine/insights`
- asks the model for cross-source, high-signal insights
- only sends notifications that clear configured confidence and urgency thresholds
- respects the user's quiet hours and channel preferences through the existing notification system

Enable it in `~/.assistant/config.toml`:

```toml
[context_engine]
enabled = true
interval_minutes = 180
```

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
