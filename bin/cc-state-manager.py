#!/usr/bin/env python3
"""
cc-term state manager — save and restore tmux sessions.
Pure Python, no external dependencies.
"""

import json
import os
import subprocess
import sys
from datetime import datetime

CC_HOME = os.path.expanduser("~/.cc-term")
STATE_DIR = os.path.join(CC_HOME, "state")
STATE_FILE = os.path.join(STATE_DIR, "snapshot.json")
TMUX_CONF = os.path.join(CC_HOME, "config/tmux.conf")
TMUX_SOCKET = "cc-term"

# Colors
C = "\033[0;36m"
G = "\033[0;32m"
Y = "\033[1;33m"
R = "\033[0;31m"
B = "\033[1m"
NC = "\033[0m"

def info(msg):  print(f"{C}[cc-term]{NC} {msg}")
def ok(msg):    print(f"{G}[cc-term]{NC} {msg}")
def warn(msg):  print(f"{Y}[cc-term]{NC} {msg}")
def err(msg):   print(f"{R}[cc-term]{NC} {msg}", file=sys.stderr)


def run_tmux(*args, check=False):
    """Run a tmux command and return stdout."""
    try:
        result = subprocess.run(
            ["tmux", "-L", TMUX_SOCKET, "-f", TMUX_CONF] + list(args),
            capture_output=True, text=True, timeout=10
        )
        if check and result.returncode != 0:
            return None
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return "" if not check else None


def tmux_has_session(name):
    """Check if a tmux session exists."""
    result = subprocess.run(
        ["tmux", "-L", TMUX_SOCKET, "-f", TMUX_CONF, "has-session", "-t", name],
        capture_output=True, timeout=5
    )
    return result.returncode == 0


def find_claude_in_pane(pane_pid):
    """Check if claude is running as a child of the pane's shell."""
    try:
        result = subprocess.run(
            ["pgrep", "-P", str(pane_pid)],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return ""

        for child_pid in result.stdout.strip().split("\n"):
            if not child_pid:
                continue
            ps = subprocess.run(
                ["ps", "-o", "command=", "-p", child_pid.strip()],
                capture_output=True, text=True, timeout=5
            )
            cmd = ps.stdout.strip()
            if "claude" in cmd.lower():
                return cmd
        return ""
    except Exception:
        return ""


# ================================================================
# SAVE
# ================================================================
def save():
    """Capture current tmux state to a JSON snapshot."""
    os.makedirs(STATE_DIR, exist_ok=True)

    sessions_raw = run_tmux("list-sessions", "-F", "#{session_name}")
    if not sessions_raw:
        warn("No tmux sessions to save.")
        return

    state = {
        "timestamp": datetime.now().isoformat(),
        "sessions": []
    }

    for session_name in sessions_raw.split("\n"):
        if not session_name:
            continue

        session = {"name": session_name, "windows": []}

        windows_raw = run_tmux(
            "list-windows", "-t", session_name, "-F",
            "#{window_index}|#{window_name}|#{window_layout}|#{window_active}"
        )

        for win_line in (windows_raw or "").split("\n"):
            if not win_line:
                continue
            parts = win_line.split("|")
            win_idx = parts[0]
            window = {
                "index": win_idx,
                "name": parts[1] if len(parts) > 1 else "",
                "layout": parts[2] if len(parts) > 2 else "",
                "active": (parts[3] == "1") if len(parts) > 3 else False,
                "panes": []
            }

            panes_raw = run_tmux(
                "list-panes", "-t", f"{session_name}:{win_idx}", "-F",
                "#{pane_index}|#{pane_current_path}|#{pane_current_command}|#{pane_active}|#{pane_pid}"
            )

            for pane_line in (panes_raw or "").split("\n"):
                if not pane_line:
                    continue
                pp = pane_line.split("|")
                pane_pid = pp[4] if len(pp) > 4 else ""
                pane_cmd = pp[2] if len(pp) > 2 else ""

                claude_cmd = ""
                if pane_pid:
                    claude_cmd = find_claude_in_pane(pane_pid)

                pane = {
                    "index": pp[0],
                    "cwd": pp[1] if len(pp) > 1 else "",
                    "command": pane_cmd,
                    "active": (pp[3] == "1") if len(pp) > 3 else False,
                    "claude_running": bool(claude_cmd),
                    "claude_cmd": claude_cmd,
                }
                window["panes"].append(pane)

            session["windows"].append(window)
        state["sessions"].append(session)

    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

    # Stats
    ns = len(state["sessions"])
    nw = sum(len(s["windows"]) for s in state["sessions"])
    np = sum(len(w["panes"]) for s in state["sessions"] for w in s["windows"])
    nc = sum(
        1 for s in state["sessions"]
        for w in s["windows"]
        for p in w["panes"]
        if p.get("claude_running")
    )

    ok(f"State saved -> {STATE_FILE}")
    info(f"  {ns} session(s), {nw} window(s), {np} pane(s)")
    if nc:
        info(f"  {nc} Claude Code session(s) detected (will resume on recover)")


# ================================================================
# RECOVER
# ================================================================
def recover():
    """Restore tmux sessions from the saved snapshot."""
    if not os.path.exists(STATE_FILE):
        err("No saved state found. Run 'cc-term save' first.")
        return False

    with open(STATE_FILE, "r") as f:
        state = json.load(f)

    ts = state.get("timestamp", "unknown")
    sessions = state.get("sessions", [])
    info(f"Recovering from snapshot: {ts}")
    info(f"  {len(sessions)} session(s) to restore")
    print()

    restored_sessions = []
    claude_targets = []

    for session in sessions:
        sname = session["name"]
        windows = session.get("windows", [])
        if not windows:
            continue

        if tmux_has_session(sname):
            warn(f"Session '{sname}' already exists, skipping.")
            continue

        # ---- Create session with the first window ----
        w0 = windows[0]
        p0 = w0["panes"][0] if w0.get("panes") else None
        cwd0 = _valid_dir(p0.get("cwd") if p0 else "")
        w0_idx = w0.get("index", "1")

        run_tmux(
            "new-session", "-d",
            "-s", sname,
            "-n", w0.get("name", ""),
            "-c", cwd0,
            "-x", "200", "-y", "50"
        )

        # Extra panes in the first window (use base index since it's the first window created)
        first_win_actual = run_tmux(
            "list-windows", "-t", sname, "-F", "#{window_index}"
        ).strip().split("\n")[0]

        _create_panes_target(sname, first_win_actual, w0, cwd0, skip_first=True)

        # Apply layout
        if w0.get("layout"):
            run_tmux("select-layout", "-t", f"{sname}:{first_win_actual}", w0["layout"])

        # Collect claude targets from first window
        for p in w0.get("panes", []):
            if p.get("claude_running") and p.get("cwd"):
                claude_targets.append((sname, first_win_actual, p["index"], p["cwd"]))

        # ---- Create remaining windows ----
        for w in windows[1:]:
            wp0 = w["panes"][0] if w.get("panes") else None
            wcwd = _valid_dir(wp0.get("cwd") if wp0 else "")

            run_tmux(
                "new-window", "-t", sname,
                "-n", w.get("name", ""),
                "-c", wcwd
            )

            # Get actual index of the window just created (last window)
            all_wins = run_tmux(
                "list-windows", "-t", sname, "-F", "#{window_index}"
            ).strip().split("\n")
            actual_idx = all_wins[-1] if all_wins else w.get("index", "1")

            _create_panes_target(sname, actual_idx, w, wcwd, skip_first=True)

            if w.get("layout"):
                run_tmux("select-layout", "-t", f"{sname}:{actual_idx}", w["layout"])

            for p in w.get("panes", []):
                if p.get("claude_running") and p.get("cwd"):
                    claude_targets.append((sname, actual_idx, p["index"], p["cwd"]))

        # Select the active window (use index 1 for first, 2 for second, etc.)
        all_wins = run_tmux(
            "list-windows", "-t", sname, "-F", "#{window_index}"
        ).strip().split("\n")
        for i, w in enumerate(windows):
            if w.get("active") and i < len(all_wins):
                run_tmux("select-window", "-t", f"{sname}:{all_wins[i]}")
                break

        restored_sessions.append(sname)
        ok(f"Session '{sname}' restored ({len(windows)} window(s))")

    # ---- Resume Claude Code sessions ----
    if claude_targets:
        print()
        info(f"Resuming {len(claude_targets)} Claude Code session(s)...")
        for sname, win_idx, pane_idx, cwd in claude_targets:
            target = f"{sname}:{win_idx}.{pane_idx}"
            safe_cwd = cwd.replace("'", "'\\''")
            run_tmux(
                "send-keys", "-t", target,
                f"cd '{safe_cwd}' && claude --dangerously-skip-permissions --continue",
                "Enter"
            )
            ok(f"  Resumed in {target} -> {cwd}")

    if restored_sessions:
        print()
        ok("Recovery complete!")
        # Output session list for the shell wrapper to open iTerm2 tabs
        print(f"__RESTORED__:{','.join(restored_sessions)}")
    else:
        warn("No sessions were restored (all already running?).")

    return bool(restored_sessions)


def _valid_dir(d):
    """Return d if it's a valid directory, else $HOME."""
    home = os.path.expanduser("~")
    if d and os.path.isdir(d):
        return d
    return home


def _create_panes_target(session, win_idx, window, fallback_cwd, skip_first=False):
    """Create panes in a window using actual window index."""
    panes = window.get("panes", [])
    for p in panes[1:] if skip_first else panes:
        cwd = _valid_dir(p.get("cwd", fallback_cwd))
        run_tmux(
            "split-window", "-t", f"{session}:{win_idx}",
            "-c", cwd
        )


# ================================================================
# SHOW (display saved state without restoring)
# ================================================================
def show():
    """Display the saved snapshot info."""
    if not os.path.exists(STATE_FILE):
        warn("No saved state. Run 'cc-term save' first.")
        return

    with open(STATE_FILE, "r") as f:
        state = json.load(f)

    ts = state.get("timestamp", "?")
    print()
    info(f"Saved snapshot: {ts}")
    print()

    for s in state.get("sessions", []):
        sname = s["name"]
        windows = s.get("windows", [])
        print(f"  {B}{sname}{NC}  ({len(windows)} window(s))")
        for w in windows:
            panes = w.get("panes", [])
            active = " *" if w.get("active") else ""
            cc_count = sum(1 for p in panes if p.get("claude_running"))
            cc_tag = f"  [{G}CC{NC}]" if cc_count else ""
            print(f"    {w['index']}:{w.get('name', '?')}  ({len(panes)} pane(s)){active}{cc_tag}")
            for p in panes:
                cc = f" {G}[claude]{NC}" if p.get("claude_running") else ""
                print(f"      pane {p['index']}: {p.get('cwd', '?')}{cc}")
        print()


# ================================================================
# MAIN
# ================================================================
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: cc-state-manager.py [save|recover|show]")
        sys.exit(1)

    action = sys.argv[1]
    if action == "save":
        save()
    elif action == "recover":
        recover()
    elif action == "show":
        show()
    else:
        err(f"Unknown action: {action}")
        sys.exit(1)
