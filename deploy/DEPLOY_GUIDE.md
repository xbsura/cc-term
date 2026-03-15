# cc-term 远程代理部署指南

## 架构

cc-term 的远程访问使用内置的 WebSocket 反向隧道，不需要 frp、ngrok 或 Cloudflare Tunnel 等第三方工具。

```
客户端 (macOS)                          服务器 (Linux)
┌─────────────────┐                    ┌─────────────────────────┐
│ ttyd (本地终端)   │                    │ cc-proxy-server.py      │
│   ↕              │                    │   port 9999             │
│ cc-tunnel-client │ ── WebSocket ──→   │   ↕                     │
│   (反向隧道)     │    (wss://)        │ Nginx (443, SSL)        │
└─────────────────┘                    │   ↕                     │
                                       │ 浏览器访问               │
                                       │ https://domain/t/<token>│
                                       └─────────────────────────┘
```

工作流程：
1. 服务器运行 `cc-proxy-server.py`，监听 9999 端口
2. Nginx 反向代理 443 → 9999，提供 SSL
3. 客户端执行 `cc-term main -r`，启动本地 ttyd + 反向隧道
4. 隧道客户端通过 WebSocket 连接到服务器，注册 session
5. 浏览器访问 `https://domain/t/<token>/` 即可操作终端

## 服务器部署

### 1. 安装依赖

```bash
apt-get update
apt-get install -y python3 python3-venv nginx certbot python3-certbot-nginx
```

### 2. 部署代理服务器

```bash
# 上传文件
scp bin/cc-proxy-server.py root@your-server:/opt/cc-term/bin/
scp -r config/ttyd root@your-server:/opt/cc-term/config/

# SSH 到服务器
ssh root@your-server

mkdir -p /opt/cc-term/{bin,config/ttyd,run}
cd /opt/cc-term
python3 -m venv venv
```

### 3. 创建 systemd 服务

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

### 4. 配置 Nginx + SSL

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

### 5. 配置客户端

在客户端 Mac 上设置环境变量指向你的服务器（可选，默认使用 ttyd.ink）：

```bash
export CC_PROXY_HOST="your-domain.com"
export CC_PROXY_PORT="443"
export CC_PROXY_PROTOCOL="https"
```

## 使用

```bash
# 注册 session 到远程代理
cc-term main -r

# 带密码保护
cc-term main -r -u admin -p secret

# 启动本地代理服务器（用于自托管或开发调试）
cc-term -server
cc-term -server --port 8080
cc-term -server --token my-secret-token
```

## 验证

```bash
# 服务器端
systemctl status cc-term-proxy
curl http://localhost:9999/api/sessions

# 客户端
cc-term main -r
# 浏览器打开输出的 URL
```

## 安全

参见 [SECURITY.md](../SECURITY.md) 了解远程访问的完整安全机制说明。

## 注意事项

1. 确保防火墙开放 80, 443 端口
2. DNS 记录指向服务器 IP
3. SSL 证书由 certbot 自动续期
4. 代理服务器可通过 `--token` 参数限制 session 注册权限
