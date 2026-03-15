# cc-term Remote Proxy Deployment Guide

## Architecture

cc-term's remote access uses a built-in WebSocket reverse tunnel — no third-party tools like frp, ngrok, or Cloudflare Tunnel required.

```
Client (macOS)                          Server (Linux)
┌─────────────────┐                    ┌─────────────────────────┐
│ ttyd (local)     │                    │ cc-proxy-server.py      │
│   ↕              │                    │   port 9999             │
│ cc-tunnel-client │ ── WebSocket ──→   │   ↕                     │
│   (reverse       │    (wss://)        │ Nginx (443, SSL)        │
│    tunnel)       │                    │   ↕                     │
└─────────────────┘                    │ Browser access           │
                                       │ https://domain/t/<token>│
                                       └─────────────────────────┘
```

Workflow:
1. Server runs `cc-proxy-server.py`, listening on port 9999
2. Nginx reverse-proxies 443 → 9999, providing SSL
3. Client runs `cc-term main -r`, starting local ttyd + reverse tunnel
4. Tunnel client connects to server via WebSocket, registering the session
5. Browser visits `https://domain/t/<token>/` to access the terminal

## Server Deployment

### 1. Install Dependencies

```bash
apt-get update
apt-get install -y python3 python3-venv nginx certbot python3-certbot-nginx
```

### 2. Deploy the Proxy Server

```bash
# Upload files
scp bin/cc-proxy-server.py root@your-server:/opt/cc-term/bin/
scp -r config/ttyd root@your-server:/opt/cc-term/config/

# SSH into the server
ssh root@your-server

mkdir -p /opt/cc-term/{bin,config/ttyd,run}
cd /opt/cc-term
python3 -m venv venv
```

### 3. Create systemd Service

```bash
cat > /etc/systemd/system/cc-term-proxy.service <<'EOF'
[Unit]
Description=cc-term Proxy Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/cc-term
ExecStart=/opt/cc-term/venv/bin/python /opt/cc-term/bin/cc-proxy-server.py --port 9999 --html /opt/cc-term/config/ttyd/index.html --data-dir /opt/cc-term/run
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable cc-term-proxy
systemctl start cc-term-proxy
```

### 4. Configure Nginx + SSL

```bash
cat > /etc/nginx/sites-available/cc-term <<'EOF'
server {
    listen 80;
    server_name your-domain.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.com;

    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:9999;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 86400;
    }
}
EOF

ln -sf /etc/nginx/sites-available/cc-term /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

certbot --nginx -d your-domain.com --non-interactive --agree-tos --email admin@your-domain.com

systemctl reload nginx
```

### 5. Configure the Client

On the client Mac, set environment variables to point to your server (optional, defaults to ttyd.ink):

```bash
export CC_PROXY_HOST="your-domain.com"
export CC_PROXY_PORT="443"
export CC_PROXY_PROTOCOL="https"
```

## Usage

```bash
# Register session to remote proxy
cc-term main -r

# With password protection
cc-term main -r -u admin -p secret

# Start a local proxy server (for self-hosting or debugging)
cc-term -server
cc-term -server --port 8080
cc-term -server --token my-secret-token
```

## Verification

```bash
# Server side
systemctl status cc-term-proxy
curl http://localhost:9999/api/sessions

# Client side
cc-term main -r
# Open the output URL in a browser
```

## Security

See [SECURITY.md](../SECURITY.md) for the full remote access security documentation.

## Notes

1. Ensure firewall allows ports 80 and 443
2. DNS records must point to the server IP
3. SSL certificates are auto-renewed by certbot
4. The proxy server can restrict session registration with the `--token` flag
