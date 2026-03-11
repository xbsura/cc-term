#!/usr/bin/env python3
"""
cc-terminal tmate manager

Creates one detached tmate relay per selected tmux session and serves a
lightweight index page listing the generated web/SSH links.
"""

import argparse
import asyncio
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from hashlib import sha1
from urllib.parse import parse_qs, urlparse


TMUX_SOCKET = "cc-terminal"


def parse_http_request(data):
    """Parse raw HTTP request bytes into method, path, headers."""
    lines = data.decode("utf-8", errors="replace").split("\r\n")
    first_line = lines[0].split(" ")
    method = first_line[0]
    path = first_line[1] if len(first_line) > 1 else "/"
    headers = {}
    for line in lines[1:]:
        if ": " in line:
            key, val = line.split(": ", 1)
            headers[key.lower()] = val
    return method, path, headers


class TmateManagerServer:
    def __init__(self, port, html_path, tmux_conf, tmate_conf, run_dir,
                 sessions=None, exclude_file=None):
        self.port = port
        self.html_content = self._load_html(html_path)
        self.tmux_conf = tmux_conf
        self.tmate_conf = tmate_conf
        self.run_dir = run_dir
        self.sessions = set(sessions or [])
        self.exclude_file = exclude_file
        self.sync_lock = asyncio.Lock()
        self.state_file = os.path.join(run_dir, "tmate-state.json")
        os.makedirs(run_dir, exist_ok=True)

    def _load_html(self, path):
        try:
            with open(path, "r") as handle:
                return handle.read()
        except FileNotFoundError:
            return "<html><body><h1>cc-terminal: tmate index not found</h1></body></html>"

    def _get_excluded_sessions(self):
        if not self.exclude_file:
            return set()
        try:
            with open(self.exclude_file, "r") as handle:
                return {line.strip() for line in handle if line.strip()}
        except FileNotFoundError:
            return set()

    def _load_state(self):
        try:
            with open(self.state_file, "r") as handle:
                return json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"sessions": {}}

    def _save_state(self, data):
        tmp_path = self.state_file + ".tmp"
        with open(tmp_path, "w") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
        os.replace(tmp_path, self.state_file)

    def _session_slug(self, name):
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", name).strip("-") or "session"
        digest = sha1(name.encode("utf-8")).hexdigest()[:8]
        return f"{safe}-{digest}"

    def _session_socket(self, name):
        return os.path.join(self.run_dir, f"tmate-{self._session_slug(name)}.sock")

    def _session_log(self, name):
        return os.path.join(self.run_dir, f"tmate-{self._session_slug(name)}.log")

    def _tmate_cmd(self, *args):
        return ["tmate", *args]

    def _tmux_cmd(self, *args):
        return ["tmux", "-L", TMUX_SOCKET, *args]

    def _run_cmd(self, cmd, timeout=10):
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return subprocess.CompletedProcess(cmd, 1, "", str(exc))

    def _tmate_display(self, socket_path, fmt):
        result = self._run_cmd(self._tmate_cmd("-S", socket_path, "display", "-p", fmt))
        if result.returncode != 0:
            return ""
        return result.stdout.strip()

    def _stop_socket(self, socket_path):
        if not socket_path:
            return
        self._run_cmd(self._tmate_cmd("-S", socket_path, "kill-server"), timeout=5)
        try:
            os.remove(socket_path)
        except FileNotFoundError:
            pass

    def _list_tmux_sessions(self):
        result = self._run_cmd(
            self._tmux_cmd(
                "list-sessions", "-F",
                "#{session_name}:#{session_windows}:#{session_attached}",
            ),
            timeout=5,
        )
        if result.returncode != 0:
            return []

        excluded = self._get_excluded_sessions()
        sessions = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split(":")
            name = parts[0]
            if self.sessions and name not in self.sessions:
                continue
            if excluded and name in excluded:
                continue
            sessions.append({
                "name": name,
                "windows": parts[1] if len(parts) > 1 else "?",
                "attached": parts[2] if len(parts) > 2 else "0",
            })
        return sessions

    def _list_tmux_windows(self, session_name):
        if not session_name:
            return []
        result = self._run_cmd(
            self._tmux_cmd(
                "list-windows", "-t", session_name, "-F",
                "#{window_index}\t#{window_name}\t#{window_active}\t#{window_zoomed_flag}",
            ),
            timeout=5,
        )
        if result.returncode != 0:
            return []

        windows = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            windows.append({
                "index": parts[0] if len(parts) > 0 else "0",
                "name": parts[1] if len(parts) > 1 else "window",
                "active": parts[2] if len(parts) > 2 else "0",
                "zoomed": parts[3] if len(parts) > 3 else "0",
            })
        return windows

    def _list_tmux_panes(self, session_name):
        if not session_name:
            return []
        result = self._run_cmd(
            self._tmux_cmd(
                "list-panes", "-t", session_name, "-F",
                "#{window_index}\t#{window_name}\t#{pane_index}\t#{pane_title}\t#{pane_active}\t#{pane_current_command}\t#{window_zoomed_flag}",
            ),
            timeout=5,
        )
        if result.returncode != 0:
            return []

        panes = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            panes.append({
                "window_index": parts[0] if len(parts) > 0 else "0",
                "window_name": parts[1] if len(parts) > 1 else "window",
                "pane_index": parts[2] if len(parts) > 2 else "0",
                "pane_title": parts[3] if len(parts) > 3 else "",
                "pane_active": parts[4] if len(parts) > 4 else "0",
                "pane_command": parts[5] if len(parts) > 5 else "",
                "window_zoomed": parts[6] if len(parts) > 6 else "0",
            })
        return panes

    def _select_tmux_target(self, session_name, window_index, pane_index=None, zoom=False):
        if not session_name or window_index in (None, ""):
            return {"ok": False, "error": "Missing session or window target"}

        window_target = f"{session_name}:{window_index}"
        select_window = self._run_cmd(self._tmux_cmd("select-window", "-t", window_target), timeout=5)
        if select_window.returncode != 0:
            return {
                "ok": False,
                "error": (select_window.stderr or select_window.stdout or "select-window failed").strip(),
            }

        if pane_index in (None, ""):
            return {"ok": True, "session": session_name, "window": str(window_index), "pane": ""}

        pane_target = f"{window_target}.{pane_index}"
        select_pane = self._run_cmd(self._tmux_cmd("select-pane", "-t", pane_target), timeout=5)
        if select_pane.returncode != 0:
            return {
                "ok": False,
                "error": (select_pane.stderr or select_pane.stdout or "select-pane failed").strip(),
            }

        if zoom:
            zoom_state = self._run_cmd(
                self._tmux_cmd("display-message", "-p", "-t", window_target, "#{window_zoomed_flag}"),
                timeout=5,
            )
            if zoom_state.returncode == 0 and zoom_state.stdout.strip() == "1":
                self._run_cmd(self._tmux_cmd("resize-pane", "-Z", "-t", pane_target), timeout=5)
            zoom_result = self._run_cmd(self._tmux_cmd("resize-pane", "-Z", "-t", pane_target), timeout=5)
            if zoom_result.returncode != 0:
                return {
                    "ok": False,
                    "error": (zoom_result.stderr or zoom_result.stdout or "resize-pane failed").strip(),
                }

        return {
            "ok": True,
            "session": session_name,
            "window": str(window_index),
            "pane": str(pane_index),
            "zoom": bool(zoom),
        }

    def _normalize_tmux_session(self, session_name):
        if not session_name:
            return {"ok": False, "error": "Missing session"}
        panes = self._list_tmux_panes(session_name)
        seen_windows = set()
        for pane in panes:
            if pane.get("window_zoomed") != "1":
                continue
            window_index = pane.get("window_index", "")
            if not window_index or window_index in seen_windows:
                continue
            pane_index = pane.get("pane_index", "0")
            target = f"{session_name}:{window_index}.{pane_index}"
            result = self._run_cmd(self._tmux_cmd("resize-pane", "-Z", "-t", target), timeout=5)
            if result.returncode != 0:
                return {"ok": False, "error": (result.stderr or result.stdout or "normalize session failed").strip()}
            seen_windows.add(window_index)
        return {"ok": True, "session": session_name}

    def _query_socket(self, socket_path):
        web_url = self._tmate_display(socket_path, "#{tmate_web}")
        if not web_url:
            return None
        return {
            "socket_path": socket_path,
            "web_url": web_url,
            "web_url_readonly": self._tmate_display(socket_path, "#{tmate_web_ro}"),
            "ssh_command": self._tmate_display(socket_path, "#{tmate_ssh}"),
            "ssh_command_readonly": self._tmate_display(socket_path, "#{tmate_ssh_ro}"),
            "status": "ready",
        }

    def _start_socket(self, session_name, socket_path):
        command = (
            "env -u TMUX tmux -L cc-terminal -f "
            f"{shlex.quote(self.tmux_conf)} new-session -A -s {shlex.quote(session_name)}"
        )
        start_cmd = self._tmate_cmd(
            "-S", socket_path,
            "-f", self.tmate_conf,
            "new-session", "-d", command,
        )

        log_path = self._session_log(session_name)
        with open(log_path, "a") as log_handle:
            try:
                result = subprocess.run(
                    start_cmd,
                    stdout=log_handle,
                    stderr=log_handle,
                    text=True,
                    timeout=10,
                    check=False,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                return {"status": "error", "error": str(exc)}

        if result.returncode != 0:
            return {"status": "error", "error": "tmate start failed"}

        wait_result = self._run_cmd(
            self._tmate_cmd("-S", socket_path, "wait", "tmate-ready"),
            timeout=30,
        )
        if wait_result.returncode != 0:
            return {
                "status": "error",
                "error": (wait_result.stderr or wait_result.stdout or "tmate not ready").strip(),
            }

        return self._query_socket(socket_path) or {
            "status": "error",
            "error": "tmate links unavailable",
        }

    def _ensure_session(self, session_name):
        socket_path = self._session_socket(session_name)
        info = self._query_socket(socket_path)
        if info:
            return info

        self._stop_socket(socket_path)
        return self._start_socket(session_name, socket_path)

    def _sync_sessions_blocking(self):
        current_sessions = self._list_tmux_sessions()
        current_names = {item["name"] for item in current_sessions}

        previous = self._load_state().get("sessions", {})
        for old_name, old_info in previous.items():
            if old_name not in current_names:
                self._stop_socket(old_info.get("socket_path") or self._session_socket(old_name))

        items = []
        next_state = {"sessions": {}, "updated_at": int(time.time())}
        for session in current_sessions:
            relay = self._ensure_session(session["name"])
            merged = dict(session)
            merged.update(relay)
            items.append(merged)
            next_state["sessions"][session["name"]] = merged

        self._save_state(next_state)
        items.sort(key=lambda item: item["name"])
        return items

    async def sync_sessions(self):
        async with self.sync_lock:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._sync_sessions_blocking)

    def http_response(self, status, content_type, body):
        if isinstance(body, str):
            body = body.encode("utf-8")
        header = (
            f"HTTP/1.1 {status}\r\n"
            f"Content-Type: {content_type}\r\n"
            "Cache-Control: no-store\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            "\r\n"
        )
        return header.encode() + body

    async def handle_client(self, reader, writer):
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
            writer.close()
            return

        method, path, headers = parse_http_request(raw)
        parsed = urlparse(path)
        route = parsed.path
        query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}

        body_data = b""
        content_length = int(headers.get("content-length", "0"))
        if content_length > 0:
            try:
                body_data = await asyncio.wait_for(reader.readexactly(content_length), timeout=10)
            except Exception:
                body_data = b""

        if route == "/api/sessions" and method == "GET":
            sessions = await self.sync_sessions()
            writer.write(self.http_response("200 OK", "application/json", json.dumps(sessions)))
            await writer.drain()
            writer.close()
            return

        if route == "/api/windows" and method == "GET":
            session_name = query.get("session", "")
            payload = self._list_tmux_windows(session_name)
            writer.write(self.http_response("200 OK", "application/json", json.dumps(payload)))
            await writer.drain()
            writer.close()
            return

        if route == "/api/panes" and method == "GET":
            session_name = query.get("session", "")
            payload = self._list_tmux_panes(session_name)
            writer.write(self.http_response("200 OK", "application/json", json.dumps(payload)))
            await writer.drain()
            writer.close()
            return

        if route == "/api/select-pane" and method == "POST":
            try:
                payload = json.loads(body_data.decode("utf-8") or "{}")
                result = self._select_tmux_target(
                    payload.get("session", ""),
                    payload.get("window", ""),
                    payload.get("pane", ""),
                    bool(payload.get("zoom", False)),
                )
                status = "200 OK" if result.get("ok") else "400 Bad Request"
                writer.write(self.http_response(status, "application/json", json.dumps(result)))
            except Exception as exc:
                writer.write(self.http_response(
                    "400 Bad Request",
                    "application/json",
                    json.dumps({"ok": False, "error": str(exc)}),
                ))
            await writer.drain()
            writer.close()
            return

        if route == "/api/normalize-session" and method == "POST":
            try:
                payload = json.loads(body_data.decode("utf-8") or "{}")
                result = self._normalize_tmux_session(payload.get("session", ""))
                status = "200 OK" if result.get("ok") else "400 Bad Request"
                writer.write(self.http_response(status, "application/json", json.dumps(result)))
            except Exception as exc:
                writer.write(self.http_response(
                    "400 Bad Request",
                    "application/json",
                    json.dumps({"ok": False, "error": str(exc)}),
                ))
            await writer.drain()
            writer.close()
            return

        if route == "/healthz" and method == "GET":
            writer.write(self.http_response("200 OK", "text/plain", "ok"))
            await writer.drain()
            writer.close()
            return

        if route == "/" and method == "GET":
            writer.write(self.http_response("200 OK", "text/html; charset=utf-8", self.html_content))
            await writer.drain()
            writer.close()
            return

        writer.write(self.http_response("404 Not Found", "text/plain", "Not found"))
        await writer.drain()
        writer.close()

    def stop_all(self):
        sessions = self._load_state().get("sessions", {})
        for name, info in sessions.items():
            self._stop_socket(info.get("socket_path") or self._session_socket(name))
        self._save_state({"sessions": {}, "updated_at": int(time.time())})

    async def run(self):
        server = await asyncio.start_server(self.handle_client, "0.0.0.0", self.port)
        async with server:
            await server.serve_forever()


def parse_sessions(raw_sessions):
    if not raw_sessions:
        return None
    return [item.strip() for item in raw_sessions.split(",") if item.strip()]


def main():
    parser = argparse.ArgumentParser(description="cc-terminal tmate manager")
    parser.add_argument("--port", type=int, default=9998)
    parser.add_argument("--html", type=str, required=True)
    parser.add_argument("--tmux-conf", type=str, required=True)
    parser.add_argument("--tmate-conf", type=str, required=True)
    parser.add_argument("--run-dir", type=str, required=True)
    parser.add_argument("--sessions", type=str, default="")
    parser.add_argument("--exclude-file", type=str, default="")
    args = parser.parse_args()

    server = TmateManagerServer(
        port=args.port,
        html_path=args.html,
        tmux_conf=args.tmux_conf,
        tmate_conf=args.tmate_conf,
        run_dir=args.run_dir,
        sessions=parse_sessions(args.sessions),
        exclude_file=args.exclude_file or None,
    )

    def _shutdown(_signum, _frame):
        server.stop_all()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        asyncio.run(server.run())
    finally:
        server.stop_all()


if __name__ == "__main__":
    main()
