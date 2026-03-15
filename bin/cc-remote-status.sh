#!/usr/bin/env bash
# cc-remote-status.sh — tmux status-right component
# Shows remote registration status for the current session
# Output: tmux-formatted string or nothing
CC_HOME="$HOME/.cc-term"
s="${1:-}"
[[ -z "$s" ]] && exit 0

safe=$(printf '%s' "$s" | tr -cs '[:alnum:]_.-' '-' | sed 's/^-*//; s/-*$//')
[[ -z "$safe" ]] && safe="session"
h=$(printf '%s' "$s" | shasum | cut -c1-8)
f="$CC_HOME/run/ttyd-${safe}-${h}.json"
[[ -f "$f" ]] || exit 0

p=$(grep -o '"pid": *[0-9]*' "$f" 2>/dev/null | grep -o '[0-9]*')
t=$(grep -o '"tunnel_pid": *[0-9]*' "$f" 2>/dev/null | grep -o '[0-9]*')

if [[ -n "$p" ]] && kill -0 "$p" 2>/dev/null; then
    if [[ -n "$t" && "$t" != "0" ]]; then
        if kill -0 "$t" 2>/dev/null; then
            printf ' #[fg=#27ae60,bold]R#[default]'
        else
            printf ' #[fg=#e74c3c]R#[default]'
        fi
    else
        printf ' #[fg=#27ae60,bold]R#[default]'
    fi
fi
