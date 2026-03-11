#!/usr/bin/env python3
"""
cc-term relay server
Proxies local connections (port 9999) to a remote cc-term server.
Pure Python, no external dependencies.
"""

import argparse
import asyncio
import base64
import hashlib
import http.client
import os
import signal
import struct
import sys
import urllib.parse

WS_MAGIC = b"258EAFA5-E914-47DA-95CA-5AB9141CE108"


def parse_remote_url(url):
    """Parse remote URL into host, port, token."""
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname
    port = parsed.port or 80
    params = urllib.parse.parse_qs(parsed.query)
    token = params.get("token", [""])[0]
    return host, port, token


def ws_accept_key(key):
    h = hashlib.sha1((key + WS_MAGIC.decode()).encode()).digest()
    return base64.b64encode(h).decode()


def ws_encode_frame(data, opcode=0x01, masked=False):
    """Encode a WebSocket frame. masked=True for client->server."""
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
    """Read a WebSocket frame."""
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


class RelayServer:
    def __init__(self, local_port, remote_host, remote_port, remote_token):
        self.local_port = local_port
        self.remote_host = remote_host
        self.remote_port = remote_port
        self.remote_token = remote_token

    def http_response(self, status, content_type, body):
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

    def _remote_path_with_token(self, path):
        """Append remote token to a path."""
        if "?" in path:
            return f"{path}&token={self.remote_token}"
        return f"{path}?token={self.remote_token}"

    async def fetch_remote(self, path):
        """Fetch content from remote server."""
        loop = asyncio.get_event_loop()

        def _fetch():
            conn = http.client.HTTPConnection(
                self.remote_host, self.remote_port, timeout=10
            )
            try:
                fetch_path = self._remote_path_with_token(path)
                conn.request("GET", fetch_path)
                resp = conn.getresponse()
                body = resp.read()
                content_type = resp.getheader("Content-Type", "text/plain")
                return resp.status, content_type, body
            finally:
                conn.close()

        return await loop.run_in_executor(None, _fetch)

    async def handle_client(self, reader, writer):
        try:
            raw = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"), timeout=10
            )
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
            writer.close()
            return

        method, path, headers = parse_http_request(raw)

        # --- WebSocket upgrade → proxy ---
        if headers.get("upgrade", "").lower() == "websocket":
            await self.proxy_websocket(reader, writer, path, headers)
            return

        # --- HTTP requests → proxy ---
        try:
            status_code, content_type, body = await self.fetch_remote(path)
            status_map = {
                200: "200 OK",
                403: "403 Forbidden",
                404: "404 Not Found",
            }
            status_text = status_map.get(status_code, str(status_code))
            writer.write(self.http_response(status_text, content_type, body))
            await writer.drain()
        except Exception as e:
            writer.write(
                self.http_response(
                    "502 Bad Gateway", "text/plain", f"Relay error: {e}"
                )
            )
            await writer.drain()

        writer.close()

    async def proxy_websocket(self, client_reader, client_writer, path, client_headers):
        """Bridge WebSocket between local client and remote server."""

        # 1. Connect to remote
        try:
            remote_reader, remote_writer = await asyncio.wait_for(
                asyncio.open_connection(self.remote_host, self.remote_port),
                timeout=10,
            )
        except Exception as e:
            client_writer.write(
                self.http_response(
                    "502 Bad Gateway", "text/plain", f"Cannot connect to remote: {e}"
                )
            )
            await client_writer.drain()
            client_writer.close()
            return

        # 2. Send WebSocket upgrade request to remote
        ws_key = base64.b64encode(os.urandom(16)).decode()
        remote_path = self._remote_path_with_token(path)

        upgrade_req = (
            f"GET {remote_path} HTTP/1.1\r\n"
            f"Host: {self.remote_host}:{self.remote_port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        remote_writer.write(upgrade_req.encode())
        await remote_writer.drain()

        # 3. Read remote upgrade response
        try:
            remote_resp = await asyncio.wait_for(
                remote_reader.readuntil(b"\r\n\r\n"), timeout=10
            )
        except Exception:
            client_writer.write(
                self.http_response(
                    "502 Bad Gateway", "text/plain", "Remote handshake failed"
                )
            )
            await client_writer.drain()
            client_writer.close()
            remote_writer.close()
            return

        if b"101" not in remote_resp:
            client_writer.write(
                self.http_response(
                    "502 Bad Gateway", "text/plain", "Remote rejected WebSocket"
                )
            )
            await client_writer.drain()
            client_writer.close()
            remote_writer.close()
            return

        # 4. Complete handshake with local client
        client_key = client_headers.get("sec-websocket-key", "")
        accept = ws_accept_key(client_key)

        handshake = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "\r\n"
        )
        client_writer.write(handshake.encode())
        await client_writer.drain()

        # 5. Bridge frames bidirectionally
        closed = False

        async def client_to_remote():
            nonlocal closed
            while not closed:
                try:
                    opcode, payload = await ws_read_frame(client_reader)
                    if opcode == 0x08:  # close
                        break
                    # Re-send as masked (client→server requires masking)
                    frame = ws_encode_frame(payload, opcode=opcode, masked=True)
                    remote_writer.write(frame)
                    await remote_writer.drain()
                except Exception:
                    break
            closed = True

        async def remote_to_client():
            nonlocal closed
            while not closed:
                try:
                    opcode, payload = await ws_read_frame(remote_reader)
                    if opcode == 0x08:  # close
                        break
                    # Send unmasked to client (server→client)
                    frame = ws_encode_frame(payload, opcode=opcode, masked=False)
                    client_writer.write(frame)
                    await client_writer.drain()
                except Exception:
                    break
            closed = True

        try:
            await asyncio.gather(client_to_remote(), remote_to_client())
        except Exception:
            pass
        finally:
            closed = True
            try:
                client_writer.close()
            except Exception:
                pass
            try:
                remote_writer.close()
            except Exception:
                pass

    async def run(self):
        server = await asyncio.start_server(
            self.handle_client, "0.0.0.0", self.local_port
        )
        async with server:
            await server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="cc-term relay server")
    parser.add_argument("--local-port", type=int, default=9999)
    parser.add_argument(
        "--remote-url",
        type=str,
        required=True,
        help="Remote cc-term URL (http://host:port/[?token=xxx])",
    )
    args = parser.parse_args()

    host, port, token = parse_remote_url(args.remote_url)

    if not host:
        print(f"Error: cannot parse host from URL: {args.remote_url}", file=sys.stderr)
        sys.exit(1)

    server = RelayServer(
        local_port=args.local_port,
        remote_host=host,
        remote_port=port,
        remote_token=token,
    )

    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))

    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
