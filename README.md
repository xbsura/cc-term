# cc-term

A client-side toolkit for programmers who use [Claude Code](https://docs.anthropic.com/en/docs/claude-code) on macOS. It creates an isolated directory with pre-configured console tools, session save/recover, and one-command remote access — so you can code from anywhere, even your phone.

## Why cc-term?

- **All-in-one toolkit** — One install gets you bat, btop, lazygit, tig, vim, tmux, and more, all themed and hotkeyed. No manual configuration needed.
- **Isolated environment** — Everything lives in `~/.cc-term`. Your system configs stay untouched.
- **Auto save & recover** — Sessions are saved every 5 minutes. Machine crash? One second to restore your full workspace, including Claude Code conversations.
- **Remote access** — Leave your desk? Publish your local sessions to the cloud with encryption and optional password protection. Default relay: [ttyd.ink](https://ttyd.ink).
- **Provider switching** — Multiple Claude Code API proxies? Configure and switch between them with `ccs`.
- **No signup, no lock-in** — No accounts, no cloud dependency. One command to start cloud coding.
- **Mobile-friendly** — Code from your phone with a touch-optimized terminal UI.

## Quick Start

**Option 1: One-line install**

```bash
curl -fsSL https://ttyd.ink/install | bash
```

**Option 2: Clone and install**

```bash
git clone https://github.com/xbsura/cc-term.git
cd cc-term
./install.sh
```

Then:

```bash
# Open a local session
cc-term main

# Open a remote session (accessible from any browser)
cc-term main -r

# Add a Claude API provider
cc-term -p -new -name myproxy -api https://your-proxy.com -key sk-xxx

# Launch Claude Code with default provider
ccs
```

## Commands

### Sessions

```bash
cc-term <name>             # Open named session in iTerm2
cc-term                    # Open "main" session
cc-term -delete <name>     # Kill session and clean up
cc-term -ls                # List active sessions
```

### Providers

```bash
cc-term -p -ls                                        # List all providers
cc-term -p -new -name <n> -api <url> -key <key>       # Add provider
cc-term -p -edit <id> -key <new-key>                   # Edit provider
cc-term -p -delete <id>                                # Remove provider
```

```bash
ccs                  # Use default provider
ccs <provider>       # Use named provider
ccs <provider> -c    # Continue last conversation
ccs default <id>     # Set the default provider
ccs -ls              # List providers
```

### Remote Access

```bash
cc-term main -r                # Publish session to cloud proxy
cc-term main -r -u admin -p secret   # With password protection
cc-term -r                     # Register current tmux session
```

For self-hosting, see [`deploy/`](deploy/) and [SECURITY.md](SECURITY.md).

### Session Save & Recover

Sessions are **automatically saved** every 5 minutes. On next launch, cc-term **auto-recovers** missing sessions and resumes Claude Code with `--continue`.

```bash
cc-term save                   # Manual save
cc-term recover                # Manual recover
cc-term show                   # Show saved state
```

### Update

```bash
cc-term update
```

## Pre-installed Tools

| Tool | Alias | Description |
|------|-------|-------------|
| `bat` | `cat` | Syntax-highlighted file viewer |
| `btop` | `top` | Interactive process monitor |
| `duf` | `df` | Disk usage viewer |
| `tig` | `gl` | Git log TUI |
| `lazygit` | `lg` | Full Git TUI |
| `vim` | — | Isolated vimrc |
| `tmux` | — | Isolated socket and config |

**Shortcuts:** `gs` (git status), `gd` (git diff), `push`/`pull` (auto-branch), `tls`/`tn`/`tx`/`tk` (tmux).

## License

[MIT](LICENSE) — use it however you want.
