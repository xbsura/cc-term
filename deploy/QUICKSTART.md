# 快速开始：部署代理服务器

## 1. 打包部署文件

在本地运行：
```bash
cd deploy
./package.sh
```

## 2. 上传到服务器

```bash
scp cc-term-deploy.tar.gz root@your-server:/tmp/
```

## 3. 在服务器上部署

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

## 4. 客户端使用

cc-term 内置了 WebSocket 反向隧道，不需要额外的隧道工具。

```bash
# 使用默认代理 (ttyd.ink)
cc-term main -r

# 使用自建代理
CC_PROXY_HOST=your-domain.com cc-term main -r
```

### 环境变量配置

���以通过环境变量指向自建代理服务器：

```bash
export CC_PROXY_HOST=your-domain.com
export CC_PROXY_PORT=443
export CC_PROXY_PROTOCOL=https

cc-term main -r
```

## 带密码保护

```bash
cc-term main -r -u myuser -p mypass
```

访问时需要输入用户名和密码。

## 本地代理模式

如果只在局域网使用，可以启动本地代理服务器：

```bash
cc-term -server
cc-term main -r
```

详细部署说明请参阅 [DEPLOY_GUIDE.md](DEPLOY_GUIDE.md)。
