# Security

This document describes the security mechanisms used in cc-term's remote access system.

## Session Registration Authentication

Every client obtains an `agg_key` / `agg_secret` credential pair from the proxy server on first connection (`POST /api/agg/new`). All subsequent session registrations (`POST /api/register`) require a valid `agg_key`/`agg_secret` pair. Invalid credentials return `403 Forbidden`.

The proxy server optionally accepts a `--token` flag at startup. When set, the `POST /api/agg/new` endpoint requires this server token before issuing new credentials, restricting who can register sessions.

## Session Access Tokens

Each registered session receives a 24-byte random hex token (`openssl rand -hex 12`, 96 bits of entropy). The session URL is `https://domain/t/<token>/`. The token is the primary access credential — brute-force guessing 96 random bits is computationally infeasible.

Invalid token requests are intentionally delayed by 3 seconds before returning a 404 response, preventing rapid enumeration.

## HTTP Basic Auth (Optional)

Sessions can be registered with `-u <username> -p <password>`. When set, the proxy server enforces HTTP Basic Auth on the `/t/<token>/` path. The browser prompts for credentials before the WebSocket connection is established.

Credentials are stored in the proxy server's process memory only — never written to disk.

## Transport Encryption

The default cloud proxy (ttyd.ink) uses HTTPS/WSS on port 443. All data, including URL tokens, is encrypted in the TLS channel. Self-hosted deployments should configure Nginx with Let's Encrypt SSL.

## WebSocket Tunnel

For remote access through NAT, `cc-tunnel-client.py` establishes a persistent WebSocket control channel to the proxy server. When a browser connects:

1. The proxy signals the tunnel client via the control channel with a random `conn_id` (128-bit hex).
2. The tunnel client opens a data channel at `/api/tunnel/data/<conn_id>`.
3. The proxy bridges the browser's WebSocket to the data channel as a raw byte relay.
4. Data channels expire after 10 seconds if unclaimed.

## In-Memory State

All session metadata, credentials, and authentication state are stored in the proxy server's process memory. A server restart clears everything, requiring clients to re-register.
