# cc-term

A macOS terminal toolkit for AI-powered coding with [Claude Code](https://docs.anthropic.com/en/docs/claude-code). Creates a fully isolated development environment with custom shell, tmux, vim configs, multi-provider API management, web-based remote access, and MCP plugin support — without touching your global dotfiles.

## Why cc-term?

Running Claude Code across multiple API providers, sessions, and devices gets messy fast. cc-term solves this by giving you:

- **Isolated environment** — everything lives in `~/.cc-term`, your system configs stay untouched
- **Provider switching** — manage multiple Claude API endpoints and swap between them with one command
- **Remote access** — open any terminal session in a browser through a cloud proxy
- **Session persistence** — automatically saves and restores your full tmux workspace every 5 minutes
- **Batteries included** — pre-configured shell, tmux, vim, plus productivity tools

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
2. Install all required packages via Homebrew
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
cc-term -ls                # List active sessions
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
ccs default <id>     # Set the default provider
ccs -ls              # List providers
```

### Remote Access

Share terminal sessions via browser through the cloud proxy ([ttyd.ink](https://ttyd.ink)).

```bash
cc-term main -r                # Register session for remote access
# → https://ttyd.ink/t/<token>/
```

**With authentication:**

```bash
cc-term main -r -u admin -p secret
```

**In-session registration** (run inside a tmux pane):

```bash
cc-term -r                     # Register current session
```

The aggregate page at the proxy root shows all registered sessions with a clean web UI. Sessions can be marked private with `-s` to hide them from the aggregate page.

For self-hosting the proxy server, see [`deploy/`](deploy/) and [SECURITY.md](SECURITY.md).

### Session Save & Recover

Sessions are **automatically saved** every 5 minutes while cc-term is running. When you open a new session, cc-term checks for saved state and **automatically recovers** any sessions that aren't already running — including resuming Claude Code conversations with `--continue`.

You can also manage state manually:

```bash
cc-term save                   # Manually snapshot all tmux sessions
cc-term recover                # Manually restore sessions and open iTerm2 tabs
cc-term show                   # Display saved state
```

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

## Pre-installed Tools

The cc-term environment comes with the following tools, all installed via Homebrew:

| Tool | Description | Usage |
|------|-------------|-------|
| `bat` | Syntax-highlighting file viewer | Aliased as `cat` — all `cat` calls use `bat` with syntax highlighting |
| `btop` | Interactive process monitor | Aliased as `top` |
| `duf` | Disk usage viewer | Aliased as `df` |
| `tig` | Git log viewer (TUI) | Aliased as `gl` |
| `lazygit` | Full Git TUI | Aliased as `lg` |
| `qrencode` | QR code generator | Used by remote access for terminal QR codes |
| `vim` | Text editor | Isolated vimrc at `~/.cc-term/config/vimrc` |
| `tmux` | Terminal multiplexer | Isolated socket `cc-term`, isolated config |

**Tmux shortcuts:** `tls` (list sessions), `tn` (new), `tx` (attach), `tk` (kill).

**Git shortcuts:** `gs` (status), `gd` (diff), `push` / `pull` (auto-detect branch).

## License

MIT
