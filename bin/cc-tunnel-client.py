#!/usr/bin/env python3
"""
cc-term tunnel client

Establishes a WebSocket reverse tunnel from the local machine to a remote
cc-term proxy server, replacing SSH reverse tunnels.

Protocol:
  Control channel: persistent WebSocket to /api/tunnel?token=TOKEN
  Data channels:   per-connection HTTP Upgrade to /api/tunnel/data/{conn_id}

Pure Python, no external dependencies.
"""

import argparse
import asyncio
import base64
import hashlib
import json
import os
import signal
import ssl
import struct
import sys
import time

WS_MAGIC = "258EAFA5-E914-47DA-95CA-5AB9141CE108"

# ---------------------------------------------------------------------------
# WebSocket helpers (client-side, frames are always masked per RFC 6455)
# ---------------------------------------------------------------------------

def ws_encode_frame(data, opcode=0x01, masked=True):
    """Encode a WebSocket frame. Client-to-server frames must be masked."""
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
        for i in range(len(masked_data)):
            masked_data[i] ^= mask_key[i % 4]
        frame.extend(masked_data)
    else:
        frame.extend(data)

    return bytes(frame)


async def ws_read_frame(reader):
    """Read and decode a single WebSocket frame."""
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
        for i in range(len(payload)):
            payload[i] ^= mask_key[i % 4]
        payload = bytes(payload)

    return opcode, payload


# ---------------------------------------------------------------------------
# Tunnel client
# ---------------------------------------------------------------------------

class TunnelClient:

    def __init__(self, server_host, server_port, use_tls, token, local_port,
                 agg_key="", agg_secret="", name=""):
        self.server_host = server_host
        self.server_port = server_port
        self.use_tls = use_tls
        self.token = token
        self.local_port = local_port
        self.agg_key = agg_key
        self.agg_secret = agg_secret
        self.name = name
        self._shutdown = False
        self._ctrl_writer = None

    # -- public entry -------------------------------------------------------

    async def run(self):
        """Connect control channel with auto-reconnect."""
        backoff = 1
        fail_start = None
        while not self._shutdown:
            try:
                await self._session()
                backoff = 1
                fail_start = None
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._log(f"control channel lost: {exc}")
                if fail_start is None:
                    fail_start = time.monotonic()
            if self._shutdown:
                break
            # After 10 min of continuous failures, back off to 30s
            if fail_start and (time.monotonic() - fail_start) > 600:
                backoff = 30
            else:
                backoff = min(backoff + 1, 3)
            self._log(f"reconnecting in {backoff}s ...")
            await asyncio.sleep(backoff)

    def shutdown(self):
        self._shutdown = True
        if self._ctrl_writer:
            try:
                self._ctrl_writer.close()
            except Exception:
                pass

    # -- internals ----------------------------------------------------------

    async def _session(self):
        """Run one control-channel session."""
        try:
            reader, writer = await self._ws_connect(
                f"/api/tunnel?token={self.token}"
            )
        except ConnectionError as exc:
            if "404" in str(exc):
                self._log("token not found (404), attempting re-register")
                await self._re_register()
                reader, writer = await self._ws_connect(
                    f"/api/tunnel?token={self.token}"
                )
            else:
                raise
        self._ctrl_writer = writer
        self._log("control channel connected")
        try:
            await self._control_loop(reader, writer)
        finally:
            self._ctrl_writer = None
            try:
                writer.close()
            except Exception:
                pass

    async def _re_register(self):
        """Re-register the tunnel session via HTTP POST /api/register."""
        ssl_ctx = self._ssl_context() if self.use_tls else None
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                self.server_host, self.server_port, ssl=ssl_ctx
            ),
            timeout=10,
        )
        body = json.dumps({
            "kind": "tunnel",
            "token": self.token,
            "agg_key": self.agg_key,
            "agg_secret": self.agg_secret,
            "name": self.name or self.token,
            "label": self.name or self.token,
            "port": 0,
        }).encode()
        req = (
            f"POST /api/register HTTP/1.1\r\n"
            f"Host: {self.server_host}:{self.server_port}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            "\r\n"
        )
        writer.write(req.encode() + body)
        await writer.drain()

        buf = b""
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                break
            buf += chunk

        writer.close()

        status_line = buf.split(b"\r\n")[0] if buf else b""
        if b"200" in status_line:
            self._log("re-registered successfully")
        elif b"403" in status_line:
            raise ConnectionError("re-register failed: invalid agg_key/agg_secret (403)")
        else:
            raise ConnectionError(
                f"re-register failed: {status_line.decode(errors='replace')}"
            )

    async def _control_loop(self, reader, writer):
        """Read JSON messages from the server on the control WebSocket."""
        while not self._shutdown:
            opcode, payload = await ws_read_frame(reader)
            if opcode == 0x08:  # close
                self._log("server sent close frame")
                break
            if opcode == 0x09:  # ping
                writer.write(ws_encode_frame(payload, opcode=0x0A, masked=True))
                await writer.drain()
                continue
            if opcode == 0x0A:  # pong — ignore
                continue
            if opcode not in (0x01, 0x02):
                continue

            try:
                msg = json.loads(payload)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

            action = msg.get("action")
            if action == "ping":
                writer.write(ws_encode_frame(
                    json.dumps({"action": "pong"}), masked=True
                ))
                await writer.drain()
            elif action == "connect":
                conn_id = msg.get("conn_id")
                if conn_id:
                    asyncio.ensure_future(self._handle_connect(conn_id))

    async def _handle_connect(self, conn_id):
        """Open data channel to server + TCP to local ttyd, bridge them."""
        self._log(f"[{conn_id[:8]}] new connection request")
        try:
            # Connect to local ttyd
            local_r, local_w = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", self.local_port),
                timeout=5,
            )
        except Exception as exc:
            self._log(f"[{conn_id[:8]}] local connect failed: {exc}")
            return

        try:
            # Open data channel to server
            data_r, data_w = await self._open_data_channel(conn_id)
        except Exception as exc:
            self._log(f"[{conn_id[:8]}] data channel failed: {exc}")
            local_w.close()
            return

        self._log(f"[{conn_id[:8]}] bridging")
        await self._bridge(local_r, local_w, data_r, data_w)
        self._log(f"[{conn_id[:8]}] closed")

    async def _open_data_channel(self, conn_id):
        """HTTP Upgrade handshake to /api/tunnel/data/{conn_id}."""
        ssl_ctx = self._ssl_context() if self.use_tls else None
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                self.server_host, self.server_port, ssl=ssl_ctx
            ),
            timeout=10,
        )

        req = (
            f"GET /api/tunnel/data/{conn_id} HTTP/1.1\r\n"
            f"Host: {self.server_host}:{self.server_port}\r\n"
            "Upgrade: tunnel\r\n"
            "Connection: Upgrade\r\n"
            "\r\n"
        )
        writer.write(req.encode())
        await writer.drain()

        # Read response until end of headers
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = await reader.read(4096)
            if not chunk:
                raise ConnectionError("data channel: server closed before 101")
            buf += chunk

        if b"101" not in buf.split(b"\r\n")[0]:
            raise ConnectionError(f"data channel: unexpected response: {buf[:200]!r}")

        # Any bytes after headers belong to the raw stream
        hdr_end = buf.index(b"\r\n\r\n") + 4
        extra = buf[hdr_end:]
        if extra:
            # Push extra bytes back — create a wrapper that yields them first
            reader = _PrefixReader(reader, extra)

        return reader, writer

    async def _ws_connect(self, path):
        """Perform WebSocket client handshake over TCP/TLS."""
        ssl_ctx = self._ssl_context() if self.use_tls else None
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                self.server_host, self.server_port, ssl=ssl_ctx
            ),
            timeout=10,
        )

        ws_key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {self.server_host}:{self.server_port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        writer.write(req.encode())
        await writer.drain()

        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = await reader.read(4096)
            if not chunk:
                raise ConnectionError("WebSocket handshake: connection closed")
            buf += chunk

        status_line = buf.split(b"\r\n")[0]
        if b"101" not in status_line:
            raise ConnectionError(
                f"WebSocket handshake failed: {status_line.decode(errors='replace')}"
            )

        # Validate Sec-WebSocket-Accept
        expected = base64.b64encode(
            hashlib.sha1((ws_key + WS_MAGIC).encode()).digest()
        ).decode()
        if expected.encode() not in buf:
            raise ConnectionError("WebSocket handshake: invalid accept key")

        # Any trailing data after headers is the first WS frame(s)
        hdr_end = buf.index(b"\r\n\r\n") + 4
        extra = buf[hdr_end:]
        if extra:
            reader = _PrefixReader(reader, extra)

        return reader, writer

    @staticmethod
    async def _bridge(a_reader, a_writer, b_reader, b_writer):
        """Bidirectional raw byte relay."""
        async def pipe(r, w):
            try:
                while True:
                    data = await r.read(65536)
                    if not data:
                        break
                    w.write(data)
                    await w.drain()
            except Exception:
                pass
            finally:
                try:
                    w.close()
                except Exception:
                    pass

        await asyncio.gather(pipe(a_reader, b_writer), pipe(b_reader, a_writer))

    def _ssl_context(self):
        ctx = ssl.create_default_context()
        # Allow self-signed certs commonly used for dev tunnels
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    @staticmethod
    def _log(msg):
        print(f"[tunnel] {msg}", file=sys.stderr, flush=True)


class _PrefixReader:
    """Wraps an asyncio StreamReader, prepending buffered bytes."""

    def __init__(self, reader, prefix):
        self._reader = reader
        self._prefix = prefix

    async def read(self, n=-1):
        if self._prefix:
            data = self._prefix[:n] if n > 0 else self._prefix
            self._prefix = self._prefix[len(data):]
            return data
        return await self._reader.read(n)

    async def readexactly(self, n):
        if self._prefix:
            if len(self._prefix) >= n:
                data = self._prefix[:n]
                self._prefix = self._prefix[n:]
                return data
            data = self._prefix
            self._prefix = b""
            remaining = await self._reader.readexactly(n - len(data))
            return data + remaining
        return await self._reader.readexactly(n)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_server_url(url):
    """Parse ws://host:port or wss://host:port into (host, port, tls)."""
    if url.startswith("wss://"):
        tls = True
        rest = url[6:]
    elif url.startswith("ws://"):
        tls = False
        rest = url[5:]
    else:
        tls = False
        rest = url

    rest = rest.rstrip("/")
    if ":" in rest:
        host, port_str = rest.rsplit(":", 1)
        port = int(port_str)
    else:
        host = rest
        port = 443 if tls else 9999

    return host, port, tls


def main():
    parser = argparse.ArgumentParser(description="cc-term tunnel client")
    parser.add_argument("--server", required=True,
                        help="Remote proxy URL (ws:// or wss://)")
    parser.add_argument("--token", required=True,
                        help="Session token for registration")
    parser.add_argument("--local-port", type=int, default=17681,
                        help="Local ttyd port to tunnel (default: 17681)")
    parser.add_argument("--agg-key", default="",
                        help="Aggregate key for re-registration")
    parser.add_argument("--agg-secret", default="",
                        help="Aggregate secret for re-registration")
    parser.add_argument("--name", default="",
                        help="Session name for re-registration")
    args = parser.parse_args()

    host, port, tls = parse_server_url(args.server)
    client = TunnelClient(host, port, tls, args.token, args.local_port,
                          agg_key=args.agg_key, agg_secret=args.agg_secret,
                          name=args.name)

    loop = asyncio.new_event_loop()

    def _signal_handler():
        client.shutdown()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    try:
        loop.run_until_complete(client.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
