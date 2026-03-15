#!/bin/bash
set -e

DOMAIN="${1:-ttyd.ink}"
EMAIL="${2:-admin@ttyd.ink}"

echo "==> Installing dependencies..."
apt-get update
apt-get install -y python3 python3-venv nginx certbot python3-certbot-nginx

echo "==> Setting up cc-term..."
mkdir -p /opt/cc-term/{bin,config/ttyd,run}
cd /opt/cc-term
python3 -m venv venv

echo "==> Creating systemd service..."
cat > /etc/systemd/system/cc-term-proxy.service <<EOF
[Unit]
Description=cc-term Proxy Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/cc-term
ExecStart=/opt/cc-term/venv/bin/python /opt/cc-term/bin/cc-proxy-server.py --port 9999 --html /opt/cc-term/config/ttyd/index.html --data-dir /opt/cc-term/run
Restart=always

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable cc-term-proxy
systemctl start cc-term-proxy

echo "==> Configuring Nginx..."
cat > /etc/nginx/sites-available/cc-term <<EOF
server {
    listen 80;
    server_name $DOMAIN;
    return 301 https://\$server_name\$request_uri;
}

server {
    listen 443 ssl http2;
    server_name $DOMAIN;

    ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:9999;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_read_timeout 86400;
    }
}
EOF

ln -sf /etc/nginx/sites-available/cc-term /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

echo "==> Obtaining SSL certificate..."
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --email "$EMAIL"

systemctl reload nginx

echo "==> Deployment complete!"
echo "Server running at https://$DOMAIN"

