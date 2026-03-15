# cc-term 远程部署指南

## 方案：使用 Cloudflare Tunnel

### 服务器端部署 (ttyd.ink)

#### 1. 安装依赖
```bash
apt-get update
apt-get install -y python3 python3-venv nginx certbot python3-certbot-nginx
```

#### 2. 部署代理服务器
```bash
mkdir -p /opt/cc-term/{bin,config/ttyd,run}
cd /opt/cc-term

# 上传文件
# scp bin/cc-proxy-server.py root@ttyd.ink:/opt/cc-term/bin/
# scp config/ttyd/index.html root@ttyd.ink:/opt/cc-term/config/ttyd/

python3 -m venv venv
```

#### 3. 创建 systemd 服务
```bash
cat > /etc/systemd/system/cc-term-proxy.service <<'EOF'
[Unit]
Description=cc-term Proxy Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/cc-term
ExecStart=/opt/cc-term/venv/bin/python /opt/cc-term/bin/cc-proxy-server.py --port 9999 --html /opt/cc-term/config/ttyd/index.html
Restart=always

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable cc-term-proxy
systemctl start cc-term-proxy
```

#### 4. 配置 Nginx + SSL
```bash
cat > /etc/nginx/sites-available/cc-term <<'EOF'
server {
    listen 80;
    server_name ttyd.ink;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name ttyd.ink;

    ssl_certificate /etc/letsencrypt/live/ttyd.ink/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ttyd.ink/privkey.pem;

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

# 申请 SSL 证书
certbot --nginx -d ttyd.ink --non-interactive --agree-tos --email admin@ttyd.ink

systemctl reload nginx
```

### 客户端配置 (本地 Mac)

#### 方案 A: 使用 Cloudflare Tunnel (推荐)

1. 安装 cloudflared:
```bash
brew install cloudflare/cloudflare/cloudflared
```

2. 登录并创建隧道:
```bash
cloudflared tunnel login
cloudflared tunnel create cc-term
```

3. 配置隧道 (~/.cloudflared/config.yml):
```yaml
tunnel: <tunnel-id>
credentials-file: /Users/<user>/.cloudflared/<tunnel-id>.json

ingress:
  - hostname: "*.ttyd.ink"
    service: http://localhost:17681
  - service: http_status:404
```

4. 运行隧道:
```bash
cloudflared tunnel run cc-term
```

#### 方案 B: 使用 ngrok

```bash
brew install ngrok
ngrok http 17681
# 使用返回的 URL 注册到代理服务器
```

### 使用方法

```bash
# 默认使用远程代理
cc-term main -r

# 使用本地代理
cc-term main -r --local

# 带密码保护
cc-term main -r -u admin -p secret123
```

## 注意事项

1. 确保防火墙开放 80, 443 端口
2. DNS 记录指向服务器 IP
3. 定期更新 SSL 证书（certbot 会自动续期）
