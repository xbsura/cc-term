#!/usr/bin/env python3
"""
cc-term proxy server

Serves an aggregate session page and routes requests to registered backends.
Supports three registration kinds:
- backend: local HTTP/WebSocket backends routed by ?token=
- tmate: metadata-only entries rendered on the aggregate page and exposed as
  local redirect URLs under /tmate/<token>
- ttyd: local ttyd backends routed by /t/<token>/ path prefix
- tunnel: remote ttyd backends connected via WebSocket reverse tunnel
"""

import argparse
import asyncio
import base64
import hashlib
import http.client
import json
import os
import secrets
import signal
import struct
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlparse

WS_MAGIC = b"258EAFA5-E914-47DA-95CA-5AB9141CE108"
TMUX_SOCKET = "cc-term"

backends = {}
tmate_sessions = {}
ttyd_sessions = {}  # token -> {"port": int, "name": str, "username": str, "password": str, ...}
agg_keys = {}  # agg_key -> agg_secret
tunnel_controls = {}  # token -> {"reader": r, "writer": w, "keepalive": task}
pending_conns = {}  # conn_id -> asyncio.Future resolving to (reader, writer)


def ws_accept_key(key):
    h = hashlib.sha1((key + WS_MAGIC.decode()).encode()).digest()
    return base64.b64encode(h).decode()


def check_basic_auth(headers, username, password):
    if not username or not password:
        return True
    auth = headers.get("authorization", "")
    if not auth.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth[6:]).decode("utf-8")
        user, pwd = decoded.split(":", 1)
        return user == username and pwd == password
    except Exception:
        return False


def ws_encode_frame(data, opcode=0x01, masked=False):
    if isinstance(data, str):
        data = data.encode("utf-8")
    length = len(data)
    frame = bytearray()
    frame.append(0x80 | opcode)
    mask_bit = 0x80 if masked else 0x00
    if length < 126:
        frame.append(mask_bit | length)
    elif length < 65536:
        frame.append(mask_bit | 126)
        frame.extend(struct.pack(">H", length))
    else:
        frame.append(mask_bit | 127)
        frame.extend(struct.pack(">Q", length))
    if masked:
        mask_key = os.urandom(4)
        frame.extend(mask_key)
        masked_data = bytearray(data)
        for index in range(len(masked_data)):
            masked_data[index] ^= mask_key[index % 4]
        frame.extend(masked_data)
    else:
        frame.extend(data)
    return bytes(frame)


async def ws_read_frame(reader):
    head = await reader.readexactly(2)
    opcode = head[0] & 0x0F
    masked = (head[1] >> 7) & 1
    length = head[1] & 0x7F
    if length == 126:
        raw = await reader.readexactly(2)
        length = struct.unpack(">H", raw)[0]
    elif length == 127:
        raw = await reader.readexactly(8)
        length = struct.unpack(">Q", raw)[0]
    mask_key = await reader.readexactly(4) if masked else None
    payload = await reader.readexactly(length)
    if mask_key:
        payload = bytearray(payload)
        for index in range(len(payload)):
            payload[index] ^= mask_key[index % 4]
        payload = bytes(payload)
    return opcode, payload


def parse_http_request(data):
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


def run_tmux_cmd(*args, timeout=5):
    try:
        return subprocess.run(
            ["tmux", "-L", TMUX_SOCKET, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(args, 1, "", str(exc))


def list_tmux_windows(session_name):
    if not session_name:
        return []
    result = run_tmux_cmd(
        "list-windows", "-t", session_name, "-F",
        "#{window_index}\t#{window_name}\t#{window_active}\t#{window_zoomed_flag}",
    )
    if result.returncode != 0:
        return []

    items = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        items.append({
            "index": parts[0] if len(parts) > 0 else "0",
            "name": parts[1] if len(parts) > 1 else "window",
            "active": parts[2] if len(parts) > 2 else "0",
            "zoomed": parts[3] if len(parts) > 3 else "0",
        })
    return items


def list_tmux_panes(session_name):
    if not session_name:
        return []
    result = run_tmux_cmd(
        "list-panes", "-t", session_name, "-F",
        "#{window_index}\t#{window_name}\t#{pane_index}\t#{pane_title}\t#{pane_active}\t#{pane_current_command}\t#{window_zoomed_flag}",
    )
    if result.returncode != 0:
        return []

    items = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        items.append({
            "window_index": parts[0] if len(parts) > 0 else "0",
            "window_name": parts[1] if len(parts) > 1 else "window",
            "pane_index": parts[2] if len(parts) > 2 else "0",
            "pane_title": parts[3] if len(parts) > 3 else "",
            "pane_active": parts[4] if len(parts) > 4 else "0",
            "pane_command": parts[5] if len(parts) > 5 else "",
            "window_zoomed": parts[6] if len(parts) > 6 else "0",
        })
    return items


def select_tmux_target(session_name, window_index, pane_index=None, zoom=False):
    if not session_name or window_index in (None, ""):
        return {"ok": False, "error": "Missing session or window target"}

    window_target = f"{session_name}:{window_index}"
    select_window = run_tmux_cmd("select-window", "-t", window_target)
    if select_window.returncode != 0:
        return {
            "ok": False,
            "error": (select_window.stderr or select_window.stdout or "select-window failed").strip(),
        }

    if pane_index in (None, ""):
        return {"ok": True, "session": session_name, "window": str(window_index), "pane": ""}

    pane_target = f"{window_target}.{pane_index}"
    select_pane = run_tmux_cmd("select-pane", "-t", pane_target)
    if select_pane.returncode != 0:
        return {
            "ok": False,
            "error": (select_pane.stderr or select_pane.stdout or "select-pane failed").strip(),
        }

    if zoom:
        zoom_state = run_tmux_cmd("display-message", "-p", "-t", window_target, "#{window_zoomed_flag}")
        if zoom_state.returncode == 0 and zoom_state.stdout.strip() == "1":
            run_tmux_cmd("resize-pane", "-Z", "-t", pane_target)
        zoom_result = run_tmux_cmd("resize-pane", "-Z", "-t", pane_target)
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


def normalize_tmux_session(session_name):
    if not session_name:
        return {"ok": False, "error": "Missing session"}
    panes = list_tmux_panes(session_name)
    seen_windows = set()
    for pane in panes:
        if pane.get("window_zoomed") != "1":
            continue
        window_index = pane.get("window_index", "")
        if not window_index or window_index in seen_windows:
            continue
        pane_index = pane.get("pane_index", "0")
        target = f"{session_name}:{window_index}.{pane_index}"
        result = run_tmux_cmd("resize-pane", "-Z", "-t", target)
        if result.returncode != 0:
            return {"ok": False, "error": (result.stderr or result.stdout or "normalize session failed").strip()}
        seen_windows.add(window_index)
    return {"ok": True, "session": session_name}


class ProxyServer:
    def __init__(self, port, token, html_path, data_dir=".", homepage_dir=None):
        self.port = port
        self.token = token
        self.data_dir = data_dir
        self.html_content = self._load_html(html_path)
        self.homepage_html = ""
        self.docs_html = ""
        if homepage_dir:
            self.homepage_html = self._load_html(os.path.join(homepage_dir, "index.html"))
            self.docs_html = self._load_html(os.path.join(homepage_dir, "docs.html"))
        self._load_agg_keys()

    def _load_html(self, path):
        try:
            with open(path, "r") as handle:
                return handle.read()
        except FileNotFoundError:
            return "<html><body><h1>cc-term: index not found</h1></body></html>"

    def _generate_install_script(self):
        return """#!/bin/bash
# cc-term installer — https://github.com/xbsura/cc-term
set -e

REPO="https://github.com/xbsura/cc-term.git"
INSTALL_DIR="$HOME/.cc-term-src"

echo ""
echo "  cc-term installer"
echo "  =================="
echo ""

# Check macOS
if [[ "$(uname)" != "Darwin" ]]; then
    echo "Error: cc-term requires macOS."
    exit 1
fi

# Clone or update
if [[ -d "$INSTALL_DIR" ]]; then
    echo "[cc-term] Updating existing installation..."
    cd "$INSTALL_DIR"
    git pull --ff-only origin main 2>/dev/null || git pull origin main
else
    echo "[cc-term] Cloning cc-term..."
    git clone "$REPO" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

echo "[cc-term] Running install.sh..."
echo ""
./install.sh

echo ""
echo "[cc-term] Installation complete!"
echo "[cc-term] Open a new iTerm2 window and run: cc-term"
echo ""
"""

    def _load_agg_keys(self):
        global agg_keys
        path = os.path.join(self.data_dir, "agg_keys.json")
        try:
            with open(path, "r") as f:
                agg_keys = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            agg_keys = {}

    def _save_agg_keys(self):
        path = os.path.join(self.data_dir, "agg_keys.json")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(agg_keys, f, indent=2)

    def http_response(self, status, content_type, body, extra_headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        header = (
            f"HTTP/1.1 {status}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Cache-Control: no-store\r\n"
            "Connection: close\r\n"
        )
        if extra_headers:
            for key, value in extra_headers.items():
                header += f"{key}: {value}\r\n"
        header += "\r\n"
        return header.encode() + body

    def _parse_path_and_query(self, raw_path):
        parsed = urlparse(raw_path)
        query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
        return parsed.path, query

    def _registration_allowed(self, query):
        if not self.token:
            return True
        return query.get("token", "") == self.token

    def _tmate_payloads(self, filter_agg_key=""):
        items = []
        for token, info in tmate_sessions.items():
            if filter_agg_key and info.get("agg_key") != filter_agg_key:
                continue
            item = dict(info)
            item["token"] = token
            item["proxy_path"] = f"/tmate/{token}"
            items.append(item)
        for token, info in ttyd_sessions.items():
            if filter_agg_key and info.get("agg_key") != filter_agg_key:
                continue
            item = dict(info)
            item["token"] = token
            item["proxy_path"] = f"/t/{token}/"
            item["web_url"] = f"/t/{token}/"
            items.append(item)
        items.sort(key=lambda item: item.get("name", item.get("label", "")))
        return items

    def _session_allowed(self, session_name):
        if not session_name:
            return False
        return (
            any(info.get("name") == session_name for info in tmate_sessions.values())
            or any(info.get("name") == session_name for info in ttyd_sessions.values())
        )

    async def handle_client(self, reader, writer):
        log = lambda msg: print(f"[proxy] {msg}", file=sys.stderr, flush=True)
        try:
            # IMPORTANT: Do NOT wrap reader.read() in asyncio.wait_for() or
            # asyncio.timeout() — both break the StreamReader in Python 3.13
            # due to internal cancellation issues, making subsequent reads
            # return EOF. Use plain await instead.
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = await reader.read(8192)
                if not chunk:
                    writer.close()
                    return
                buf += chunk
            hdr_end = buf.index(b"\r\n\r\n") + 4
            raw = buf[:hdr_end]
            extra = buf[hdr_end:]  # body data that arrived with headers
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            writer.close()
            return

        method, raw_path, headers = parse_http_request(raw)
        path, query = self._parse_path_and_query(raw_path)
        peername = writer.get_extra_info("peername", ("?", 0))
        log(f"{method} {raw_path} from {peername[0]}:{peername[1]}")
        if headers.get("upgrade", "").lower() == "websocket":
            log("  -> WebSocket upgrade detected")

        body_data = b""
        content_length = int(headers.get("content-length", "0"))
        if content_length > 0:
            body_data = extra
            remaining = content_length - len(body_data)
            if remaining > 0:
                try:
                    body_data += await reader.readexactly(remaining)
                except Exception:
                    pass

        if path == "/api/agg/new" and method == "POST":
            if not self._registration_allowed(query):
                writer.write(self.http_response("403 Forbidden", "text/plain", "Invalid registration token"))
                await writer.drain()
                writer.close()
                return
            new_key = secrets.token_hex(6)
            new_secret = secrets.token_hex(16)
            agg_keys[new_key] = new_secret
            self._save_agg_keys()
            writer.write(self.http_response(
                "200 OK", "application/json",
                json.dumps({"agg_key": new_key, "agg_secret": new_secret}),
            ))
            await writer.drain()
            writer.close()
            return

        if path == "/api/register" and method == "POST":
            if not self._registration_allowed(query):
                writer.write(self.http_response("403 Forbidden", "text/plain", "Invalid registration token"))
                await writer.drain()
                writer.close()
                return
            await self._handle_register(writer, body_data)
            return

        # Tunnel control channel: WebSocket from tunnel client
        if path == "/api/tunnel" and headers.get("upgrade", "").lower() == "websocket":
            tunnel_token = query.get("token", "")
            info = ttyd_sessions.get(tunnel_token)
            if info and info.get("kind") == "tunnel":
                await self._handle_tunnel_control(reader, writer, headers, tunnel_token)
            else:
                writer.write(self.http_response("404 Not Found", "text/plain", "No tunnel session for this token"))
                await writer.drain()
                writer.close()
            return

        # Tunnel data channel: per-connection raw pipe from tunnel client
        if path.startswith("/api/tunnel/data/") and headers.get("upgrade", "").lower() == "tunnel":
            conn_id = path.rsplit("/", 1)[-1]
            if conn_id in pending_conns:
                await self._handle_tunnel_data(reader, writer, conn_id)
            else:
                writer.write(self.http_response("404 Not Found", "text/plain", "No pending connection"))
                await writer.drain()
                writer.close()
            return

        if path == "/api/unregister" and method == "POST":
            if not self._registration_allowed(query):
                writer.write(self.http_response("403 Forbidden", "text/plain", "Invalid registration token"))
                await writer.drain()
                writer.close()
                return
            await self._handle_unregister(writer, body_data)
            return

        if path == "/api/backends" and method == "GET":
            result = {
                token: {"label": info["label"], "port": info["port"]}
                for token, info in backends.items()
            }
            writer.write(self.http_response("200 OK", "application/json", json.dumps(result)))
            await writer.drain()
            writer.close()
            return

        if path == "/api/sessions" and method == "GET":
            filter_agg_key = query.get("agg_key", "")
            writer.write(self.http_response("200 OK", "application/json", json.dumps(self._tmate_payloads(filter_agg_key))))
            await writer.drain()
            writer.close()
            return

        if path == "/api/windows" and method == "GET":
            session_name = query.get("session", "")
            if not self._session_allowed(session_name):
                writer.write(self.http_response("404 Not Found", "application/json", json.dumps([])))
            else:
                writer.write(self.http_response("200 OK", "application/json", json.dumps(list_tmux_windows(session_name))))
            await writer.drain()
            writer.close()
            return

        if path == "/api/panes" and method == "GET":
            session_name = query.get("session", "")
            if not self._session_allowed(session_name):
                writer.write(self.http_response("404 Not Found", "application/json", json.dumps([])))
            else:
                writer.write(self.http_response("200 OK", "application/json", json.dumps(list_tmux_panes(session_name))))
            await writer.drain()
            writer.close()
            return

        if path == "/api/select-pane" and method == "POST":
            try:
                payload = json.loads(body_data.decode("utf-8") or "{}")
                session_name = payload.get("session", "")
                if not self._session_allowed(session_name):
                    result = {"ok": False, "error": "Session not found"}
                    writer.write(self.http_response("404 Not Found", "application/json", json.dumps(result)))
                else:
                    result = select_tmux_target(
                        session_name,
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

        if path == "/api/normalize-session" and method == "POST":
            try:
                payload = json.loads(body_data.decode("utf-8") or "{}")
                session_name = payload.get("session", "")
                if not self._session_allowed(session_name):
                    result = {"ok": False, "error": "Session not found"}
                    writer.write(self.http_response("404 Not Found", "application/json", json.dumps(result)))
                else:
                    result = normalize_tmux_session(session_name)
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

        if path == "/" and method == "GET" and "token" not in query:
            content = self.homepage_html or self.html_content
            writer.write(self.http_response("200 OK", "text/html; charset=utf-8", content))
            await writer.drain()
            writer.close()
            return

        if path == "/docs" and method == "GET" and self.docs_html:
            writer.write(self.http_response("200 OK", "text/html; charset=utf-8", self.docs_html))
            await writer.drain()
            writer.close()
            return

        if path == "/install" and method == "GET":
            install_script = self._generate_install_script()
            writer.write(self.http_response("200 OK", "text/plain; charset=utf-8", install_script))
            await writer.drain()
            writer.close()
            return

        # Support aggregate page with agg_key path
        if method == "GET" and path.startswith("/") and path.count("/") == 1 and len(path) > 1:
            path_key = path[1:]
            if path_key in agg_keys:
                writer.write(self.http_response("200 OK", "text/html; charset=utf-8", self.html_content))
                await writer.drain()
                writer.close()
                return

        if path.startswith("/t/"):
            parts = path.split("/", 3)  # ['', 't', token, ...]
            ttyd_token = parts[2] if len(parts) > 2 else ""
            info = ttyd_sessions.get(ttyd_token)
            if not info:
                writer.write(self.http_response("404 Not Found", "text/plain", "Session not found"))
                await writer.drain()
                writer.close()
                return

            # Check authentication if session is protected
            if info.get("protected"):
                if not check_basic_auth(headers, info.get("username", ""), info.get("password", "")):
                    writer.write(self.http_response(
                        "401 Unauthorized",
                        "text/plain",
                        "Authentication required",
                        {"WWW-Authenticate": 'Basic realm="cc-term"'}
                    ))
                    await writer.drain()
                    writer.close()
                    return

            if info.get("kind") == "tunnel":
                backend_rw = await self._get_tunnel_backend(ttyd_token)
                if backend_rw is None:
                    writer.write(self.http_response(
                        "504 Gateway Timeout", "text/plain",
                        "Tunnel client not connected or timed out"
                    ))
                    await writer.drain()
                    writer.close()
                    return
                tunnel_r, tunnel_w = backend_rw
                if headers.get("upgrade", "").lower() == "websocket":
                    await self._proxy_websocket_tunnel(
                        reader, writer, raw_path, headers, tunnel_r, tunnel_w
                    )
                else:
                    await self._proxy_http_tunnel(
                        writer, method, raw_path, headers, body_data, tunnel_r, tunnel_w
                    )
                return

            backend_port = info["port"]
            if headers.get("upgrade", "").lower() == "websocket":
                await self._proxy_websocket(reader, writer, raw_path, headers, backend_port)
                return
            await self._proxy_http(writer, raw_path, backend_port)
            return

        if path.startswith("/tmate/") and method == "GET":
            token = path.split("/", 2)[2]
            info = tmate_sessions.get(token)
            if not info or not info.get("web_url"):
                writer.write(self.http_response("404 Not Found", "text/plain", "Session not found"))
            else:
                writer.write(self.http_response(
                    "302 Found",
                    "text/plain",
                    f"Redirecting to {info['web_url']}",
                    {"Location": info["web_url"]},
                ))
            await writer.drain()
            writer.close()
            return

        token = query.get("token", "")
        if not token or token not in backends:
            writer.write(self.http_response(
                "404 Not Found", "text/plain", "No backend registered for this token.\n"
            ))
            await writer.drain()
            writer.close()
            return

        backend_port = backends[token]["port"]
        backend_query = {key: value for key, value in query.items() if key != "token"}
        if backend_query:
            backend_path = path + "?" + "&".join(f"{key}={value}" for key, value in backend_query.items())
        else:
            backend_path = path

        if headers.get("upgrade", "").lower() == "websocket":
            await self._proxy_websocket(reader, writer, backend_path, headers, backend_port)
            return

        await self._proxy_http(writer, backend_path, backend_port)

    async def _handle_register(self, writer, body_data):
        try:
            data = json.loads(body_data.decode())
            kind = data.get("kind", "backend")
            if kind == "tmate":
                name = data.get("name") or data.get("label")
                web_url = data.get("web_url", "")
                if not name or not web_url:
                    raise ValueError("tmate registration requires name and web_url")

                for token, info in list(tmate_sessions.items()):
                    if info.get("name") == name:
                        del tmate_sessions[token]

                token = secrets.token_urlsafe(16)
                tmate_sessions[token] = {
                    "kind": "tmate",
                    "name": name,
                    "label": data.get("label", name),
                    "web_url": web_url,
                    "web_url_readonly": data.get("web_url_readonly", ""),
                    "ssh_command": data.get("ssh_command", ""),
                    "ssh_command_readonly": data.get("ssh_command_readonly", ""),
                    "attached": data.get("attached", "0"),
                    "windows": data.get("windows", "?"),
                    "created_at": int(time.time()),
                }
                writer.write(self.http_response(
                    "200 OK",
                    "application/json",
                    json.dumps({"token": token, "path": f"/tmate/{token}"}),
                ))
            elif kind == "ttyd":
                name = data.get("name") or data.get("label")
                ttyd_port = int(data.get("port", 0))
                token = data.get("token", "") or secrets.token_hex(12)
                username = data.get("username", "")
                password = data.get("password", "")
                if not name or not ttyd_port:
                    raise ValueError("ttyd registration requires name and port")

                # agg_key/agg_secret validation
                req_agg_key = data.get("agg_key", "")
                req_agg_secret = data.get("agg_secret", "")
                if not req_agg_key or agg_keys.get(req_agg_key) != req_agg_secret:
                    writer.write(self.http_response(
                        "403 Forbidden", "application/json",
                        json.dumps({"error": "invalid agg_key/agg_secret"}),
                    ))
                    await writer.drain()
                    return

                if token not in ttyd_sessions:
                    for old_token, info in list(ttyd_sessions.items()):
                        if info.get("name") == name:
                            del ttyd_sessions[old_token]

                ttyd_sessions[token] = {
                    "kind": "ttyd",
                    "name": name,
                    "label": data.get("label", name),
                    "port": ttyd_port,
                    "attached": data.get("attached", "0"),
                    "windows": data.get("windows", "?"),
                    "created_at": int(time.time()),
                    "username": username,
                    "password": password,
                    "protected": bool(username and password),
                    "agg_key": req_agg_key,
                }
                writer.write(self.http_response(
                    "200 OK",
                    "application/json",
                    json.dumps({"token": token, "path": f"/t/{token}/"}),
                ))
            elif kind == "tunnel":
                name = data.get("name") or data.get("label")
                token = data.get("token", "") or secrets.token_hex(12)
                username = data.get("username", "")
                password = data.get("password", "")
                if not name:
                    raise ValueError("tunnel registration requires name")

                # agg_key/agg_secret validation
                req_agg_key = data.get("agg_key", "")
                req_agg_secret = data.get("agg_secret", "")
                if not req_agg_key or agg_keys.get(req_agg_key) != req_agg_secret:
                    writer.write(self.http_response(
                        "403 Forbidden", "application/json",
                        json.dumps({"error": "invalid agg_key/agg_secret"}),
                    ))
                    await writer.drain()
                    return

                if token not in ttyd_sessions:
                    for old_token, info in list(ttyd_sessions.items()):
                        if info.get("name") == name:
                            del ttyd_sessions[old_token]
                            ctrl = tunnel_controls.pop(old_token, None)
                            if ctrl:
                                try:
                                    ctrl["keepalive"].cancel()
                                    ctrl["writer"].close()
                                except Exception:
                                    pass

                ttyd_sessions[token] = {
                    "kind": "tunnel",
                    "name": name,
                    "label": data.get("label", name),
                    "port": 0,
                    "attached": data.get("attached", "0"),
                    "windows": data.get("windows", "?"),
                    "created_at": int(time.time()),
                    "username": username,
                    "password": password,
                    "protected": bool(username and password),
                    "agg_key": req_agg_key,
                }
                writer.write(self.http_response(
                    "200 OK",
                    "application/json",
                    json.dumps({"token": token, "path": f"/t/{token}/"}),
                ))
            else:
                backend_port = int(data["port"])
                label = data.get("label", "")
                token = secrets.token_urlsafe(16)
                backends[token] = {"port": backend_port, "label": label}
                writer.write(self.http_response(
                    "200 OK", "application/json", json.dumps({"token": token})
                ))
        except Exception as exc:
            writer.write(self.http_response("400 Bad Request", "text/plain", str(exc)))
        await writer.drain()
        writer.close()

    async def _handle_unregister(self, writer, body_data):
        try:
            data = json.loads(body_data.decode())
            token = data.get("token", "")
            removed = False
            if token in backends:
                del backends[token]
                removed = True
            if token in tmate_sessions:
                del tmate_sessions[token]
                removed = True
            if token in ttyd_sessions:
                if ttyd_sessions[token].get("kind") == "tunnel":
                    ctrl = tunnel_controls.pop(token, None)
                    if ctrl:
                        try:
                            ctrl["keepalive"].cancel()
                            ctrl["writer"].close()
                        except Exception:
                            pass
                del ttyd_sessions[token]
                removed = True
            if removed:
                writer.write(self.http_response("200 OK", "text/plain", "OK"))
            else:
                writer.write(self.http_response("404 Not Found", "text/plain", "Token not found"))
        except Exception as exc:
            writer.write(self.http_response("400 Bad Request", "text/plain", str(exc)))
        await writer.drain()
        writer.close()

    # ------------------------------------------------------------------
    # Tunnel support
    # ------------------------------------------------------------------

    async def _handle_tunnel_control(self, reader, writer, headers, token):
        """Handle the persistent WebSocket control channel from a tunnel client."""
        log = lambda msg: print(f"[tunnel-ctrl] {msg}", file=sys.stderr, flush=True)

        # Complete WebSocket handshake
        ws_key = headers.get("sec-websocket-key", "")
        accept = ws_accept_key(ws_key)
        resp = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "\r\n"
        )
        writer.write(resp.encode())
        await writer.drain()
        log(f"control channel connected for token {token[:8]}...")

        # Keepalive task
        async def keepalive():
            try:
                while True:
                    await asyncio.sleep(30)
                    writer.write(ws_encode_frame(
                        json.dumps({"action": "ping"}), opcode=0x01, masked=False
                    ))
                    await writer.drain()
            except (asyncio.CancelledError, Exception):
                pass

        ka_task = asyncio.ensure_future(keepalive())
        tunnel_controls[token] = {"reader": reader, "writer": writer, "keepalive": ka_task}

        try:
            while True:
                opcode, payload = await ws_read_frame(reader)
                if opcode == 0x08:  # close
                    log(f"tunnel client sent close ({token[:8]}...)")
                    break
                if opcode == 0x09:  # ping
                    writer.write(ws_encode_frame(payload, opcode=0x0A, masked=False))
                    await writer.drain()
                    continue
                if opcode == 0x0A:  # pong
                    continue
                # text/binary — expect JSON
                if opcode in (0x01, 0x02):
                    try:
                        msg = json.loads(payload)
                    except Exception:
                        continue
                    if msg.get("action") == "pong":
                        pass  # keepalive ack
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            log(f"tunnel client disconnected ({token[:8]}...)")
        finally:
            ka_task.cancel()
            tunnel_controls.pop(token, None)
            try:
                writer.close()
            except Exception:
                pass

    async def _handle_tunnel_data(self, reader, writer, conn_id):
        """Accept a data channel connection from the tunnel client."""
        log = lambda msg: print(f"[tunnel-data] {msg}", file=sys.stderr, flush=True)

        # Complete the Upgrade: tunnel handshake
        resp = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: tunnel\r\n"
            "Connection: Upgrade\r\n"
            "\r\n"
        )
        writer.write(resp.encode())
        await writer.drain()
        log(f"data channel ready for {conn_id[:8]}...")

        future = pending_conns.get(conn_id)
        if future and not future.done():
            future.set_result((reader, writer))
        else:
            log(f"no pending connection for {conn_id[:8]}...")
            writer.close()

    async def _get_tunnel_backend(self, token):
        """Signal the tunnel client to open a data channel, wait for it."""
        log = lambda msg: print(f"[tunnel] {msg}", file=sys.stderr, flush=True)

        ctrl = tunnel_controls.get(token)
        if not ctrl:
            log(f"no tunnel client for token {token[:8]}...")
            return None

        conn_id = secrets.token_hex(16)
        future = asyncio.get_event_loop().create_future()
        pending_conns[conn_id] = future

        try:
            msg = json.dumps({"action": "connect", "conn_id": conn_id})
            ctrl["writer"].write(ws_encode_frame(msg, opcode=0x01, masked=False))
            await ctrl["writer"].drain()
        except Exception as exc:
            log(f"failed to signal tunnel client: {exc}")
            pending_conns.pop(conn_id, None)
            return None

        try:
            result = await asyncio.wait_for(future, timeout=10)
            return result
        except asyncio.TimeoutError:
            log(f"tunnel data channel timeout for {conn_id[:8]}...")
            pending_conns.pop(conn_id, None)
            return None

    async def _proxy_websocket_tunnel(self, client_reader, client_writer,
                                       raw_path, client_headers,
                                       backend_reader, backend_writer):
        """WebSocket proxy using a tunnel data channel as the backend."""
        log = lambda msg: print(f"[ws-tunnel] {msg}", file=sys.stderr, flush=True)

        # Reconstruct WebSocket upgrade and send through tunnel to ttyd
        ws_key = client_headers.get("sec-websocket-key", "")
        ws_version = client_headers.get("sec-websocket-version", "13")
        ws_protocol = client_headers.get("sec-websocket-protocol", "")
        ws_extensions = client_headers.get("sec-websocket-extensions", "")
        upgrade_req = (
            f"GET {raw_path} HTTP/1.1\r\n"
            "Host: localhost\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            f"Sec-WebSocket-Version: {ws_version}\r\n"
        )
        if ws_protocol:
            upgrade_req += f"Sec-WebSocket-Protocol: {ws_protocol}\r\n"
        if ws_extensions:
            upgrade_req += f"Sec-WebSocket-Extensions: {ws_extensions}\r\n"
        upgrade_req += "\r\n"

        log("forwarding upgrade through tunnel")
        backend_writer.write(upgrade_req.encode())
        await backend_writer.drain()

        # Read backend 101 response
        resp_buf = b""
        try:
            while b"\r\n\r\n" not in resp_buf:
                chunk = await backend_reader.read(8192)
                if not chunk:
                    break
                resp_buf += chunk
        except Exception as exc:
            log(f"tunnel backend handshake FAILED: {exc}")
            client_writer.write(self.http_response("502 Bad Gateway", "text/plain", "Tunnel backend handshake failed"))
            await client_writer.drain()
            client_writer.close()
            backend_writer.close()
            return

        if b"101" not in resp_buf:
            log(f"tunnel backend rejected WS: {resp_buf[:200]!r}")
            client_writer.write(self.http_response("502 Bad Gateway", "text/plain", "Tunnel backend rejected WebSocket"))
            await client_writer.drain()
            client_writer.close()
            backend_writer.close()
            return

        resp_end = resp_buf.index(b"\r\n\r\n") + 4
        resp_headers = resp_buf[:resp_end]
        resp_extra = resp_buf[resp_end:]

        log("tunnel backend 101 OK, forwarding to client")
        client_writer.write(resp_headers)
        if resp_extra:
            client_writer.write(resp_extra)
        await client_writer.drain()

        # Raw byte relay
        async def pipe(tag, r, w):
            try:
                while True:
                    data = await r.read(65536)
                    if not data:
                        log(f"{tag}: EOF")
                        break
                    w.write(data)
                    await w.drain()
            except Exception as exc:
                log(f"{tag}: {type(exc).__name__}: {exc}")
            finally:
                try:
                    w.close()
                except Exception:
                    pass

        await asyncio.gather(
            pipe("c2t", client_reader, backend_writer),
            pipe("t2c", backend_reader, client_writer),
        )

    async def _proxy_http_tunnel(self, client_writer, method, raw_path, client_headers,
                                  body_data, backend_reader, backend_writer):
        """HTTP proxy using a tunnel data channel as the backend."""
        log = lambda msg: print(f"[http-tunnel] {msg}", file=sys.stderr, flush=True)

        # Reconstruct the HTTP request and send through tunnel
        req = f"{method} {raw_path} HTTP/1.1\r\nHost: localhost\r\n"
        for key in ("accept", "accept-encoding", "accept-language", "user-agent",
                     "cache-control", "cookie", "referer"):
            val = client_headers.get(key)
            if val:
                req += f"{key}: {val}\r\n"
        if body_data:
            req += f"Content-Length: {len(body_data)}\r\n"
        req += "Connection: close\r\n\r\n"

        log(f"forwarding {method} {raw_path} through tunnel")
        backend_writer.write(req.encode())
        if body_data:
            backend_writer.write(body_data)
        await backend_writer.drain()

        # Read complete response from backend
        resp_buf = b""
        try:
            while True:
                chunk = await backend_reader.read(65536)
                if not chunk:
                    break
                resp_buf += chunk
        except Exception as exc:
            log(f"tunnel backend response error: {exc}")

        if resp_buf:
            client_writer.write(resp_buf)
        else:
            client_writer.write(self.http_response("502 Bad Gateway", "text/plain", "Tunnel backend error"))
        await client_writer.drain()
        client_writer.close()
        backend_writer.close()

    # ------------------------------------------------------------------

    async def _proxy_http(self, writer, raw_path, backend_port):
        loop = asyncio.get_event_loop()

        def _fetch():
            conn = http.client.HTTPConnection("localhost", backend_port, timeout=10)
            try:
                conn.request("GET", raw_path)
                resp = conn.getresponse()
                body = resp.read()
                content_type = resp.getheader("Content-Type", "text/plain")
                return resp.status, content_type, body
            finally:
                conn.close()

        try:
            status_code, content_type, body = await loop.run_in_executor(None, _fetch)
            status_map = {200: "200 OK", 302: "302 Found", 403: "403 Forbidden", 404: "404 Not Found"}
            status_text = status_map.get(status_code, str(status_code))
            writer.write(self.http_response(status_text, content_type, body))
        except Exception as exc:
            writer.write(self.http_response("502 Bad Gateway", "text/plain", f"Proxy error: {exc}"))
        await writer.drain()
        writer.close()

    async def _proxy_websocket(self, client_reader, client_writer, raw_path, client_headers, backend_port):
        """Transparent WebSocket proxy: forward exact handshake, raw byte relay."""
        log = lambda msg: print(f"[ws-proxy] {msg}", file=sys.stderr, flush=True)

        log(f"connecting to backend localhost:{backend_port}")
        try:
            remote_reader, remote_writer = await asyncio.wait_for(
                asyncio.open_connection("localhost", backend_port), timeout=10
            )
        except Exception as exc:
            log(f"backend connect FAILED: {exc}")
            client_writer.write(self.http_response("502 Bad Gateway", "text/plain", f"Backend error: {exc}"))
            await client_writer.drain()
            client_writer.close()
            return

        log("connected to backend")

        # Forward client's WebSocket upgrade to backend with proper header casing.
        # parse_http_request lowercases keys, so we reconstruct with correct casing
        # since libwebsockets (ttyd) may be case-sensitive.
        ws_key = client_headers.get("sec-websocket-key", "")
        ws_version = client_headers.get("sec-websocket-version", "13")
        ws_protocol = client_headers.get("sec-websocket-protocol", "")
        ws_extensions = client_headers.get("sec-websocket-extensions", "")
        upgrade_req = (
            f"GET {raw_path} HTTP/1.1\r\n"
            f"Host: localhost:{backend_port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            f"Sec-WebSocket-Version: {ws_version}\r\n"
        )
        if ws_protocol:
            upgrade_req += f"Sec-WebSocket-Protocol: {ws_protocol}\r\n"
        if ws_extensions:
            upgrade_req += f"Sec-WebSocket-Extensions: {ws_extensions}\r\n"
        upgrade_req += "\r\n"

        log(f"forwarding upgrade to backend")
        remote_writer.write(upgrade_req.encode())
        await remote_writer.drain()

        # Read backend 101 response — NO timeout wrappers on read() (Python 3.13)
        resp_buf = b""
        try:
            while b"\r\n\r\n" not in resp_buf:
                chunk = await remote_reader.read(8192)
                if not chunk:
                    break
                resp_buf += chunk
        except Exception as exc:
            log(f"backend handshake FAILED: {exc}")
            client_writer.write(self.http_response("502 Bad Gateway", "text/plain", "Backend handshake failed"))
            await client_writer.drain()
            client_writer.close()
            remote_writer.close()
            return

        if b"101" not in resp_buf:
            log(f"backend rejected WS: {resp_buf[:200]!r}")
            client_writer.write(self.http_response("502 Bad Gateway", "text/plain", "Backend rejected WebSocket"))
            await client_writer.drain()
            client_writer.close()
            remote_writer.close()
            return

        # Split headers from any trailing data (first WS frame may be bundled)
        resp_end = resp_buf.index(b"\r\n\r\n") + 4
        resp_headers = resp_buf[:resp_end]
        resp_extra = resp_buf[resp_end:]

        log("backend 101 OK, forwarding to client")

        # Forward exact backend 101 response to client
        client_writer.write(resp_headers)
        if resp_extra:
            client_writer.write(resp_extra)
        await client_writer.drain()

        # Raw byte relay
        async def pipe(tag, r, w):
            try:
                while True:
                    data = await r.read(65536)
                    if not data:
                        log(f"{tag}: EOF")
                        break
                    w.write(data)
                    await w.drain()
            except Exception as exc:
                log(f"{tag}: {type(exc).__name__}: {exc}")
            finally:
                try:
                    w.close()
                except Exception:
                    pass

        await asyncio.gather(
            pipe("c2r", client_reader, remote_writer),
            pipe("r2c", remote_reader, client_writer),
        )

    async def run(self):
        server = await asyncio.start_server(self.handle_client, "0.0.0.0", self.port)
        async with server:
            await server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="cc-term proxy server")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--token", type=str, default="", help="Optional token to protect registration API")
    parser.add_argument("--html", type=str, required=True, help="Aggregate index HTML path")
    parser.add_argument("--data-dir", type=str, default=".", help="Directory for persistent data (agg_keys.json)")
    parser.add_argument("--homepage-dir", type=str, default="", help="Directory containing homepage index.html and docs.html")
    args = parser.parse_args()

    server = ProxyServer(
        port=args.port, token=args.token, html_path=args.html,
        data_dir=args.data_dir, homepage_dir=args.homepage_dir or None,
    )
    signal.signal(signal.SIGTERM, lambda _s, _f: sys.exit(0))
    signal.signal(signal.SIGINT, lambda _s, _f: sys.exit(0))

    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
