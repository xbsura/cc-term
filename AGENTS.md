# Repository Guidelines

## Project Structure & Module Organization
This repository is a small macOS-focused terminal toolkit. Keep changes scoped to the existing entry points:
- `install.sh` installs dependencies and deploys files into `~/.cc-term`.
- `bin/` contains the main launchers and Python utilities, including `_cc-term-core`, `cc-provider-manager.py`, `cc-state-manager.py`, `cc-remote-server.py`, and `cc-relay-server.py`.
- `providers/` stores provider-specific Bash adapters; each script exposes a small command interface such as `name` and `quota`.
- `config/` contains isolated shell, tmux, Vim, and remote UI assets copied during install.

## Build, Test, and Development Commands
There is no compiled build step. Use targeted checks instead:
- `./install.sh` — install the toolkit into `~/.cc-term` on macOS.
- `./bin/_cc-term-core -h` — verify the main CLI entry point and available subcommands.
- `python3 bin/cc-provider-manager.py list-fast` — quick provider-manager smoke test without health checks.
- `python3 bin/cc-state-manager.py show` — verify saved-state parsing.
- `bash -n install.sh bin/_cc-term-core providers/*.sh` — shell syntax validation.
- `python3 -m py_compile bin/*.py` — Python syntax validation.

## Coding Style & Naming Conventions
Follow the existing lightweight scripting style:
- Use 4-space indentation in Bash and Python.
- Prefer standard-library Python only; avoid adding third-party dependencies.
- Keep constants uppercase (for example `CC_HOME`, `STATE_FILE`) and functions in `snake_case`.
- Match existing file naming: hyphenated shell entry points in `bin/`, lowercase provider scripts in `providers/`.
- Preserve the repo’s macOS-first assumptions and isolated `~/.cc-term` paths.

## Testing Guidelines
This repo does not currently include an automated test suite. Before opening a PR, run the syntax checks above and perform a focused smoke test for the command you changed. For UI changes under `config/remote/index.html`, also verify the page loads through the remote server locally.

## Commit & Pull Request Guidelines
Git history is not available in this checkout, so use clear imperative commit messages such as `Add relay URL validation` or `Fix provider quota parsing`. Keep commits focused. PRs should include a short summary, affected commands or files, manual verification steps, and screenshots only when changing the remote web UI.

## Security & Configuration Tips
Do not commit real provider tokens, local state, or copied files from `~/.cc-term`. Redact sample URLs and secrets in examples, and keep provider-specific behavior inside `providers/` rather than hard-coding credentials in shared scripts.
