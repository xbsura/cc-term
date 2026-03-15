#!/bin/bash
# Deploy cc-term proxy server on remote host with SSL

set -e

DOMAIN="ttyd.ink"
EMAIL="admin@ttyd.ink"
INSTALL_DIR="/opt/cc-term"

echo "==> Installing dependencies..."
apt-get update
apt-get install -y python3 python3-venv nginx certbot python3-certbot-nginx

echo "==> Creating installation directory..."
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

echo "==> Setting up Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

echo "==> Copying proxy server files..."
# Assume files are uploaded to /tmp/cc-term-deploy/
cp /tmp/cc-term-deploy/cc-proxy-server.py bin/
chmod +x bin/cc-proxy-server.py

echo "==> Creating systemd service..."
cat > /etc/systemd/system/cc-term-proxy.service <<EOF
[Unit]
Description=cc-term Proxy Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/bin/cc-proxy-server.py --port 9999 --html $INSTALL_DIR/config/ttyd/index.html
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

echo "==> Configuring nginx..."
cp /tmp/cc-term-deploy/nginx.conf /etc/nginx/sites-available/cc-term
ln -sf /etc/nginx/sites-available/cc-term /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

echo "==> Obtaining SSL certificate..."
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --email "$EMAIL"

echo "==> Starting services..."
systemctl daemon-reload
systemctl enable cc-term-proxy
systemctl start cc-term-proxy
systemctl reload nginx

echo "==> Deployment complete!"
echo "Proxy server running at https://$DOMAIN"
