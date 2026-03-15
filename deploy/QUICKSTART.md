# Quick Start: Deploy the Proxy Server

## 1. Package Deployment Files

Run locally:
```bash
cd deploy
./package.sh
```

## 2. Upload to Server

```bash
scp cc-term-deploy.tar.gz root@your-server:/tmp/
```

## 3. Deploy on Server

```bash
ssh root@your-server
cd /tmp
tar -xzf cc-term-deploy.tar.gz
cd cc-term-deploy
cp bin/cc-proxy-server.py /opt/cc-term/bin/
cp config/ttyd/index.html /opt/cc-term/config/ttyd/
cd deploy
./setup-server.sh your-domain.com admin@your-domain.com
```

## 4. Client Usage

cc-term has a built-in WebSocket reverse tunnel — no additional tunnel tools needed.

```bash
# Use default proxy (ttyd.ink)
cc-term main -r

# Use self-hosted proxy
CC_PROXY_HOST=your-domain.com cc-term main -r
```

### Environment Variables

You can point to a self-hosted proxy server via environment variables:

```bash
export CC_PROXY_HOST=your-domain.com
export CC_PROXY_PORT=443
export CC_PROXY_PROTOCOL=https

cc-term main -r
```

## Password Protection

```bash
cc-term main -r -u myuser -p mypass
```

Username and password will be required to access the session.

## Local Proxy Mode

For LAN-only usage, start a local proxy server:

```bash
cc-term -server
cc-term main -r
```

For detailed deployment instructions, see [DEPLOY_GUIDE.md](DEPLOY_GUIDE.md).
