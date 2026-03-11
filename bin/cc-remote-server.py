#!/usr/bin/env python3
"""
cc-term remote server
Pure Python WebSocket terminal server (no external dependencies).
Serves a mobile-optimized web terminal that connects to tmux sessions.
"""

import asyncio
import argparse
import base64
import fcntl
import hashlib
import json
import os
import pty
import select
import signal
import struct
import sys
import termios
import threading

WS_MAGIC = b"258EAFA5-E914-47DA-95CA-5AB9141CE108"


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


def ws_accept_key(key):
    """Compute Sec-WebSocket-Accept value."""
    h = hashlib.sha1((key + WS_MAGIC.decode()).encode()).digest()
    return base64.b64encode(h).decode()


def ws_encode_frame(data, opcode=0x01):
    """Encode a WebSocket frame (server -> client, no masking)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    length = len(data)
    frame = bytearray()
    frame.append(0x80 | opcode)  # FIN + opcode
    if length < 126:
        frame.append(length)
    elif length < 65536:
        frame.append(126)
        frame.extend(struct.pack(">H", length))
    else:
        frame.append(127)
        frame.extend(struct.pack(">Q", length))
    frame.extend(data)
    return bytes(frame)


async def ws_read_frame(reader):
    """Read and decode a WebSocket frame from the client."""
    head = await reader.readexactly(2)
    fin = (head[0] >> 7) & 1
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
        for i in range(len(payload)):
            payload[i] ^= mask_key[i % 4]
        payload = bytes(payload)

    return opcode, payload


def set_pty_size(fd, rows, cols):
    """Set PTY window size."""
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)


class RemoteTerminalServer:
    def __init__(self, port, token, html_path, bash_profile, sessions=None, exclude_file=None):
        self.port = port
        self.token = token
        self.html_content = self._load_html(html_path)
        self.bash_profile = bash_profile
        self.sessions = sessions  # list of session names or None for all
        self.exclude_file = exclude_file  # path to file listing excluded session names
        self.active_connections = set()

    def _load_html(self, path):
        try:
            with open(path, "r") as f:
                return f.read()
        except FileNotFoundError:
            return "<html><body><h1>cc-term: index.html not found</h1></body></html>"

    def _get_excluded_sessions(self):
        """Read excluded session names from file (re-read each time for live updates)."""
        if not self.exclude_file:
            return set()
        try:
            with open(self.exclude_file, "r") as f:
                return {line.strip() for line in f if line.strip()}
        except FileNotFoundError:
            return set()

    def get_tmux_sessions(self):
        """Get list of tmux sessions."""
        try:
            import subprocess
            result = subprocess.run(
                ["tmux", "-L", "cc-term", "list-sessions", "-F", "#{session_name}:#{session_windows}:#{session_attached}"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                return []
            sessions = []
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split(":")
                name = parts[0]
                windows = parts[1] if len(parts) > 1 else "?"
                attached = parts[2] if len(parts) > 2 else "0"
                if self.sessions and name not in self.sessions:
                    continue
                excluded = self._get_excluded_sessions()
                if excluded and name in excluded:
                    continue
                sessions.append({
                    "name": name,
                    "windows": windows,
                    "attached": attached
                })
            return sessions
        except Exception:
            return []

    def get_tmux_windows(self, session_name):
        """Get list of windows in a tmux session."""
        try:
            import subprocess
            result = subprocess.run(
                ["tmux", "-L", "cc-term", "list-windows", "-t", session_name, "-F",
                 "#{window_index}:#{window_name}:#{window_active}"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                return []
            windows = []
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split(":")
                windows.append({
                    "index": parts[0],
                    "name": parts[1] if len(parts) > 1 else "?",
                    "active": parts[2] if len(parts) > 2 else "0"
                })
            return windows
        except Exception:
            return []

    def http_response(self, status, content_type, body):
        """Build HTTP response bytes."""
        if isinstance(body, str):
            body = body.encode("utf-8")
        header = (
            f"HTTP/1.1 {status}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        )
        return header.encode() + body

    async def handle_client(self, reader, writer):
        """Handle incoming TCP connection."""
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
            writer.close()
            return

        method, path, headers = parse_http_request(raw)

        # Parse query string
        query = {}
        if "?" in path:
            path_part, qs = path.split("?", 1)
            for param in qs.split("&"):
                if "=" in param:
                    k, v = param.split("=", 1)
                    query[k] = v
            path = path_part

        # Token authentication
        req_token = query.get("token", "")

        # --- WebSocket upgrade ---
        if path == "/ws" and headers.get("upgrade", "").lower() == "websocket":
            if req_token != self.token:
                writer.write(self.http_response("403 Forbidden", "text/plain", "Invalid token"))
                await writer.drain()
                writer.close()
                return
            session = query.get("session", "")
            await self.handle_websocket(reader, writer, headers, session)
            return

        # --- API: list sessions ---
        if path == "/api/sessions":
            if req_token != self.token:
                writer.write(self.http_response("403 Forbidden", "text/plain", "Invalid token"))
                await writer.drain()
                writer.close()
                return
            sessions = self.get_tmux_sessions()
            body = json.dumps(sessions)
            writer.write(self.http_response("200 OK", "application/json", body))
            await writer.drain()
            writer.close()
            return

        # --- API: list windows ---
        if path == "/api/windows":
            if req_token != self.token:
                writer.write(self.http_response("403 Forbidden", "text/plain", "Invalid token"))
                await writer.drain()
                writer.close()
                return
            session = query.get("session", "")
            windows = self.get_tmux_windows(session)
            body = json.dumps(windows)
            writer.write(self.http_response("200 OK", "application/json", body))
            await writer.drain()
            writer.close()
            return

        # --- HTML page ---
        if path == "/":
            if req_token != self.token:
                writer.write(self.http_response("403 Forbidden", "text/html",
                    "<html><body><h1>403 - Invalid or missing token</h1></body></html>"))
                await writer.drain()
                writer.close()
                return
            writer.write(self.http_response("200 OK", "text/html; charset=utf-8", self.html_content))
            await writer.drain()
            writer.close()
            return

        # --- 404 ---
        writer.write(self.http_response("404 Not Found", "text/plain", "Not found"))
        await writer.drain()
        writer.close()

    async def handle_websocket(self, reader, writer, headers, session_name):
        """Handle WebSocket connection — bridge to PTY running tmux."""
        # WebSocket handshake
        key = headers.get("sec-websocket-key", "")
        accept = ws_accept_key(key)

        handshake = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "\r\n"
        )
        writer.write(handshake.encode())
        await writer.drain()

        # Spawn PTY
        master_fd, slave_fd = pty.openpty()

        # Set initial size
        set_pty_size(master_fd, 24, 80)

        pid = os.fork()
        if pid == 0:
            # Child process
            os.close(master_fd)
            os.setsid()
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            if slave_fd > 2:
                os.close(slave_fd)

            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            env["CC_REMOTE"] = "1"

            if session_name:
                # Attach to specific tmux session (cc-term socket)
                tmux_conf = os.path.expanduser("~/.cc-term/config/tmux.conf")
                os.execvpe("tmux", [
                    "tmux", "-L", "cc-term", "-f", tmux_conf,
                    "new-session", "-A", "-s", session_name
                ], env)
            else:
                # Start bash with our profile (user can attach tmux manually)
                os.execvpe("bash", ["bash", "--rcfile", self.bash_profile], env)
            sys.exit(0)

        # Parent process
        os.close(slave_fd)

        self.active_connections.add(id(writer))

        # Make master_fd non-blocking
        import fcntl as fcntl_mod
        flags = fcntl_mod.fcntl(master_fd, fcntl_mod.F_GETFL)
        fcntl_mod.fcntl(master_fd, fcntl_mod.F_SETFL, flags | os.O_NONBLOCK)

        loop = asyncio.get_event_loop()
        closed = False

        async def pty_to_ws():
            """Read from PTY, send to WebSocket."""
            nonlocal closed
            while not closed:
                try:
                    data = await loop.run_in_executor(None, lambda: pty_read(master_fd))
                    if data is None:
                        break
                    if data:
                        frame = ws_encode_frame(data, opcode=0x02)  # binary
                        writer.write(frame)
                        await writer.drain()
                except (ConnectionError, OSError):
                    break
            closed = True

        async def ws_to_pty():
            """Read from WebSocket, write to PTY."""
            nonlocal closed
            while not closed:
                try:
                    opcode, payload = await ws_read_frame(reader)

                    if opcode == 0x08:  # close
                        break
                    elif opcode == 0x09:  # ping
                        writer.write(ws_encode_frame(payload, opcode=0x0A))
                        await writer.drain()
                    elif opcode in (0x01, 0x02):  # text or binary
                        # First byte is message type
                        if len(payload) < 1:
                            continue
                        msg_type = payload[0]
                        msg_data = payload[1:]

                        if msg_type == 0:  # input
                            os.write(master_fd, msg_data)
                        elif msg_type == 1:  # resize
                            try:
                                size = json.loads(msg_data.decode())
                                cols = size.get("cols", 80)
                                rows = size.get("rows", 24)
                                set_pty_size(master_fd, rows, cols)
                            except (json.JSONDecodeError, ValueError):
                                pass
                except (asyncio.IncompleteReadError, ConnectionError, OSError):
                    break
            closed = True

        try:
            await asyncio.gather(pty_to_ws(), ws_to_pty())
        except Exception:
            pass
        finally:
            closed = True
            self.active_connections.discard(id(writer))
            try:
                os.close(master_fd)
            except OSError:
                pass
            try:
                os.kill(pid, signal.SIGTERM)
                os.waitpid(pid, 0)
            except (OSError, ChildProcessError):
                pass
            try:
                # Send close frame
                writer.write(ws_encode_frame(b"", opcode=0x08))
                await writer.drain()
            except Exception:
                pass
            writer.close()

    async def run(self):
        server = await asyncio.start_server(
            self.handle_client, "0.0.0.0", self.port
        )
        # Print nothing here — the shell wrapper handles output
        async with server:
            await server.serve_forever()


def pty_read(fd):
    """Blocking read from PTY fd. Returns data or None on EOF."""
    try:
        r, _, _ = select.select([fd], [], [], 0.1)
        if fd in r:
            data = os.read(fd, 4096)
            if not data:
                return None
            return data
        return b""
    except OSError:
        return None


def main():
    parser = argparse.ArgumentParser(description="cc-term remote server")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", type=str, default="")
    parser.add_argument("--html", type=str, required=True)
    parser.add_argument("--bash-profile", type=str, required=True)
    parser.add_argument("--sessions", type=str, default="",
                        help="Comma-separated list of tmux session names to expose")
    parser.add_argument("--exclude-file", type=str, default="",
                        help="Path to file listing session names to exclude (one per line)")
    args = parser.parse_args()

    sessions = [s.strip() for s in args.sessions.split(",") if s.strip()] if args.sessions else None
    exclude_file = args.exclude_file if args.exclude_file else None

    server = RemoteTerminalServer(
        port=args.port,
        token=args.token,
        html_path=args.html,
        bash_profile=args.bash_profile,
        sessions=sessions,
        exclude_file=exclude_file,
    )

    # Handle SIGTERM gracefully
    def handle_signal(sig, frame):
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
