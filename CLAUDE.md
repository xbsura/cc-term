# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

cc-term is a **macOS-only** terminal toolkit for AI-powered coding. It creates an isolated environment at `~/.cc-term` (`CC_HOME`) with custom Bash, tmux, Vim configs, a Claude Code API provider management system, remote web-terminal access (ttyd/tmate), and MCP plugin management. It never modifies the user's global dotfiles.

## Build & Validation

There is no compiled build step. Use these checks:

```bash
# Install into ~/.cc-term (macOS only)
./install.sh

# Shell syntax validation
bash -n install.sh bin/_cc-term-core providers/*.sh

# Python syntax validation
python3 -m py_compile bin/*.py

# Smoke tests
python3 bin/cc-provider-manager.py list-fast   # provider manager (no network calls)
python3 bin/cc-state-manager.py show           # state manager

# CLI help
./bin/cc-term -h
```

There is **no automated test suite**. Verify changes with the syntax checks above plus manual smoke testing of the affected command.

## Architecture

### Isolation Model

- **`CC_HOME = ~/.cc-term`** — all runtime state, binaries, configs, and plugins live here
- **tmux socket `cc-term`** — all tmux commands use `-L cc-term` so sessions are fully isolated from any system tmux
- **`config/bash_profile`** — loaded via `exec bash --rcfile` in new iTerm2 tabs; defines `ccs()` function, PATH, aliases, and completions

### CLI Entry Points

`bin/cc-term` is a thin Bash wrapper that dispatches to:
- `bin/_cc-term-core` — the core CLI (~1000 lines Bash) handling sessions, remote access, state save/recover, plugins, updates
- `bin/cc-provider-manager.py` — provider CRUD, health checks, quota queries

### Provider System

Manages multiple Claude API proxy endpoints:
1. **`providers.json`** (in `CC_HOME`) — stores provider configs: `name`, `api` (URL), `key`, `app_id`
2. **`cc-provider-manager.py`** — CRUD operations, health checks (`/v1/messages`), generates `provider_env.sh`
3. **`provider_env.sh`** (in `CC_HOME`) — sourced in shell, exports `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN`
4. **`ccs <provider-name>`** — shell function that switches provider and launches `claude --dangerously-skip-permissions`

### Provider Adapter Scripts (`providers/*.sh`)

Each adapter implements a standard interface:
- `<script>.sh name` — print provider display name
- `<script>.sh quota --url <url> --token <token> [--app-id <id>]` — return JSON with `remaining`, `today_tokens`, `total_cost`, optionally `model_usage`
- Any unknown action — print `unsupported`

The adapter is matched by comparing `ANTHROPIC_BASE_URL` against the URL pattern in the script.

### Remote Access Stack

```
cc-term -server    →  cc-proxy-server.py  (port 9999, aggregate web page)
cc-term -r <name>  →  ttyd (per-session, dynamic port 17681+)
                           registered via POST /api/register → proxied at /t/<token>/
```

- `cc-proxy-server.py` — aggregate proxy with session index page, routes to ttyd/tmate backends
- `cc-remote-server.py` — pure-Python PTY WebSocket terminal server (alternative to ttyd)
- `cc-relay-server.py` — WebSocket relay proxy to a remote cc-term server
- `cc-tmate-manager.py` — tmate session manager with HTTP index server
- Registration tokens use `openssl rand -hex 12`

### Plugin System

Built-in catalog of MCP servers (filesystem, github, brave-search, fetch, memory, puppeteer, postgres, sqlite, sequential-thinking). Installs/uninstalls via `claude mcp add/remove` with `npx -y`.

## Coding Conventions

- **4-space indentation** in both Bash and Python
- **Python: stdlib only** — no third-party dependencies
- **Constants:** `UPPER_SNAKE_CASE` (e.g., `CC_HOME`, `STATE_FILE`)
- **Functions:** `snake_case`
- **File naming:** hyphenated entry points in `bin/` (e.g., `cc-remote-server.py`); lowercase provider scripts in `providers/`
- **macOS-first:** assumes `osascript`, `ipconfig`, `arch -arm64`, `pbcopy`, iTerm2, Homebrew. Do not introduce Linux-specific paths
- Inline Python snippets in Bash use `python3 -c` with env vars passed via exported names (e.g., `TOKEN="$token" python3 -c "import os; ..."`)

## Files Requiring Extra Care

- `config/bash_profile` — loaded live in every iTerm2 tab; changes affect all active sessions
- `config/tmux.conf` — uses isolated TPM plugin path `~/.cc-term/tmux/plugins`; keep `run '~/.cc-term/tmux/plugins/tpm/tpm'` as the last line
- `install.sh` — the macOS pre-flight check and `brew_run()` Rosetta 2 logic is intentional; don't simplify it
