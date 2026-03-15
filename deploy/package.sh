#!/bin/bash
# Package files for remote deployment

PACKAGE_DIR="cc-term-deploy"
rm -rf "$PACKAGE_DIR"
mkdir -p "$PACKAGE_DIR"/{bin,config/ttyd,deploy}

echo "==> Copying files..."
cp ../bin/cc-proxy-server.py "$PACKAGE_DIR/bin/"
cp ../config/ttyd/index.html "$PACKAGE_DIR/config/ttyd/"
cp setup-server.sh nginx.conf "$PACKAGE_DIR/deploy/"

echo "==> Creating tarball..."
tar -czf cc-term-deploy.tar.gz "$PACKAGE_DIR"

echo "==> Package created: cc-term-deploy.tar.gz"
echo ""
echo "Deploy with:"
echo "  scp cc-term-deploy.tar.gz root@ttyd.ink:/tmp/"
echo "  ssh root@ttyd.ink"
echo "  cd /tmp && tar -xzf cc-term-deploy.tar.gz"
echo "  cd cc-term-deploy/deploy && ./setup-server.sh ttyd.ink admin@ttyd.ink"
