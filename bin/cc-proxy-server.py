#!/usr/bin/env python3
"""
cc-term proxy server

Serves an aggregate session page and routes requests to registered backends.
Supports three registration kinds:
- backend: local HTTP/WebSocket backends routed by ?token=
- tmate: metadata-only entries rendered on the aggregate page and exposed as
  local redirect URLs under /tmate/<token>
- ttyd: local ttyd backends routed by /t/<token>/ path prefix
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
ttyd_sessions = {}  # token -> {"port": int, "name": str, ...}


def ws_accept_key(key):
    h = hashlib.sha1((key + WS_MAGIC.decode()).encode()).digest()
    return base64.b64encode(h).decode()


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
    def __init__(self, port, token, html_path):
        self.port = port
        self.token = token
        self.html_content = self._load_html(html_path)

    def _load_html(self, path):
        try:
            with open(path, "r") as handle:
                return handle.read()
        except FileNotFoundError:
            return "<html><body><h1>cc-term: index not found</h1></body></html>"

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

    def _tmate_payloads(self):
        items = []
        for token, info in tmate_sessions.items():
            item = dict(info)
            item["token"] = token
            item["proxy_path"] = f"/tmate/{token}"
            items.append(item)
        for token, info in ttyd_sessions.items():
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

        if path == "/api/register" and method == "POST":
            if not self._registration_allowed(query):
                writer.write(self.http_response("403 Forbidden", "text/plain", "Invalid registration token"))
                await writer.drain()
                writer.close()
                return
            await self._handle_register(writer, body_data)
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
            writer.write(self.http_response("200 OK", "application/json", json.dumps(self._tmate_payloads())))
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
                if not name or not ttyd_port:
                    raise ValueError("ttyd registration requires name and port")

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
    args = parser.parse_args()

    server = ProxyServer(port=args.port, token=args.token, html_path=args.html)
    signal.signal(signal.SIGTERM, lambda _s, _f: sys.exit(0))
    signal.signal(signal.SIGINT, lambda _s, _f: sys.exit(0))

    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
