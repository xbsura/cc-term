# Deploy cc-term Proxy Server

## Quick Deploy

```bash
# 1. Upload files to server
scp bin/cc-proxy-server.py root@your-server:/tmp/
scp -r config/ttyd root@your-server:/tmp/

# 2. SSH into server and run setup script
ssh root@your-server
bash /tmp/setup-server.sh your-domain.com admin@your-domain.com
```

For detailed steps, see [DEPLOY_GUIDE.md](DEPLOY_GUIDE.md).

## Files

| File | Description |
|------|-------------|
| `setup-server.sh` | One-click deploy script (installs deps, configures systemd, Nginx, SSL) |
| `deploy.sh` | Deploy script (manual steps version) |
| `nginx.conf` | Nginx config template |
| `package.sh` | Package deployment files |
| `publish.sh` | Publish script |

## Server Architecture

```
cc-proxy-server.py (port 9999)
  ├── Accepts client WebSocket reverse tunnel connections
  ├── Manages session registration (agg_key/agg_secret auth)
  ├── Proxies browser WebSocket to local ttyd
  └── Serves session aggregation page
          ↕
Nginx (port 443, SSL)
  └── Reverse-proxies all requests to 9999
```

## Client Usage

```bash
# Use default proxy (ttyd.ink)
cc-term main -r

# Use self-hosted proxy
CC_PROXY_HOST=your-domain.com cc-term main -r

# With password protection
cc-term main -r -u admin -p secret
```
