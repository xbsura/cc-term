# cc-term

A macOS terminal toolkit for AI-powered coding with [Claude Code](https://docs.anthropic.com/en/docs/claude-code). Creates a fully isolated development environment with custom shell, tmux, vim configs, multi-provider API management, web-based remote access, and MCP plugin support — without touching your global dotfiles.

## Why cc-term?

Running Claude Code across multiple API providers, sessions, and devices gets messy fast. cc-term solves this by giving you:

- **Isolated environment** — everything lives in `~/.cc-term`, your system configs stay untouched
- **Provider switching** — manage multiple Claude API endpoints and swap between them with one command
- **Remote access** — open any terminal session in a browser, locally or through a cloud proxy
- **Session persistence** — save and restore your full tmux workspace across reboots
- **Batteries included** — pre-configured shell, tmux, vim, plus productivity tools like bat, lazygit, btop

## Quick Start

```bash
# Clone and install
git clone https://github.com/xbsura/cc-term.git
cd cc-term
./install.sh

# Open a session
cc-term main

# Add a Claude API provider
cc-term -p -new -name myproxy -api https://your-proxy.com -key sk-xxx

# Launch Claude Code with that provider
ccs myproxy
```

## Installation

> **Requires macOS** with [iTerm2](https://iterm2.com) installed.

```bash
./install.sh
```

The installer will:
1. Install Homebrew if needed (with Rosetta 2 support on Apple Silicon)
2. Install packages: `bash`, `tmux`, `vim`, `bat`, `btop`, `duf`, `tig`, `lazygit`, `qrencode`, `python@3`
3. Create an isolated Python venv at `~/.cc-term/venv`
4. Deploy all configs to `~/.cc-term`
5. Install Tmux Plugin Manager (TPM)
6. Symlink `cc-term` into your PATH

Nothing is written outside of `~/.cc-term` and the Homebrew prefix.

## Commands

### Sessions

```bash
cc-term <name>             # Open named session in iTerm2
cc-term                    # Open "main" session
cc-term -delete <name>     # Kill session and clean up
cc-term -ls                # List active remote sessions
```

### Providers

Manage multiple Claude API proxy endpoints:

```bash
cc-term -p -ls                                        # List all providers (with health check)
cc-term -p -new -name <n> -api <url> -key <key>       # Add provider
cc-term -p -edit <id> -key <new-key>                   # Edit provider
cc-term -p -delete <id>                                # Remove provider
```

Then launch Claude Code with any provider:

```bash
ccs                  # Use default provider
ccs <provider>       # Use named provider
ccs <provider> -c    # Continue last conversation
ccs -ls              # List providers
```

The `ccs` function displays quota info on startup (if the provider adapter supports it) and sets `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` automatically.

### Remote Access

Share terminal sessions via browser — either on your local network or through a cloud proxy.

**Local network:**

```bash
cc-term -server                # Start aggregate proxy on port 9999
cc-term main -r --local        # Register session for local access
# → http://192.168.x.x:9999/t/<token>/
```

**Cloud proxy (via [ttyd.ink](https://ttyd.ink)):**

```bash
cc-term main -r                # Register through default cloud proxy
# → https://ttyd.ink/t/<token>/
```

**With authentication:**

```bash
cc-term main -r -u admin -p secret
```

**In-session registration** (run inside a tmux pane):

```bash
cc-term -r                     # Register current session
cc-term -r --local             # Register on local proxy instead
```

The aggregate page at the proxy root shows all registered sessions with a clean web UI. Sessions can be marked private with `-s` to hide them from the aggregate page.

### Session Save & Recover

```bash
cc-term save                   # Snapshot all tmux sessions
cc-term recover                # Restore sessions and open iTerm2 tabs
cc-term show                   # Display saved state
```

Captures window layout, pane arrangement, working directories, and running commands.

### Plugins (MCP Servers)

```bash
cc-term plugins                    # List available MCP servers
cc-term plugin install <name>      # Install plugin
cc-term plugin uninstall <name>    # Remove plugin
```

Available plugins: `filesystem`, `github`, `brave-search`, `fetch`, `memory`, `puppeteer`, `postgres`, `sqlite`, `sequential-thinking`.

### Update

```bash
cc-term update
```

Updates Claude Code CLI, Homebrew packages, tmux plugins, and config files in one step.

## Architecture

```
~/.cc-term/
├── bin/                        # CLI tools and servers
│   ├── cc-term                 # Entry point (thin dispatcher)
│   ├── _cc-term-core           # Core CLI logic
│   ├── cc-provider-manager.py  # Provider CRUD & health checks
│   ├── cc-proxy-server.py      # Aggregate web proxy server
│   ├── cc-tunnel-client.py     # WebSocket reverse tunnel client
│   ├── cc-state-manager.py     # Session save/recover
│   └── cc-remote-server.py     # Pure-Python PTY WebSocket server
├── config/
│   ├── bash_profile            # Shell env, aliases, ccs function
│   ├─��� tmux.conf               # Isolated tmux config
│   ├── vimrc                   # Isolated vim config
│   ├── ttyd/                   # Aggregate page template
│   └── homepage/               # Public homepage files
├── providers/                  # Provider adapter scripts
│   └── *.sh                    # Each implements name/quota interface
├── run/                        # Runtime state (PIDs, metadata)
├── state/                      # Saved session snapshots
├── venv/                       # Isolated Python environment
└── tmux/plugins/               # TPM plugins
```

### Isolation Model

| Layer | Mechanism |
|-------|-----------|
| Shell | `exec bash --rcfile ~/.cc-term/config/bash_profile` |
| Tmux | Dedicated socket: `tmux -L cc-term` |
| Vim | Custom vimrc loaded per session |
| Python | Separate venv at `~/.cc-term/venv` |
| History | `~/.cc-term/.bash_history` |

### Remote Access Stack

```
Browser ──→ Proxy Server (port 9999 or ttyd.ink:443)
                │
                ├── /t/<token>/  ──→  ttyd (local) or tunnel (remote)
                ├── /<agg_key>   ──→  Aggregate session page
                └── /api/...     ──→  Registration, session metadata
```

For cloud access, `cc-tunnel-client.py` maintains a WebSocket reverse tunnel to the proxy server, eliminating the need for SSH tunnels or port forwarding.

### Provider Adapter System

Each provider adapter in `providers/*.sh` implements:

```bash
./provider.sh name                                      # Display name
./provider.sh quota --url <url> --token <t> [--app-id <id>]  # JSON quota info
```

This enables per-provider quota display, cost tracking, and model usage breakdowns in the `ccs` startup banner.

## Shell Environment

The custom bash profile provides:

| Alias | Tool |
|-------|------|
| `cat` | `bat` (syntax highlighting) |
| `top` | `btop` (interactive monitor) |
| `df` | `duf` (disk usage) |
| `gs` | `git status` |
| `gd` | `git diff` |
| `gl` | `tig` (git log viewer) |
| `lg` | `lazygit` |

Tmux shortcuts: `tls` (list), `tn` (new), `tx` (attach), `tk` (kill).

## Deployment

To self-host the cloud proxy, see [`deploy/`](deploy/) for nginx configs, setup scripts, and deployment guides. The default public proxy is [ttyd.ink](https://ttyd.ink).

## License

MIT
