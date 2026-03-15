# 快速开始：部署到 ttyd.ink

## 1. 打包部署文件

在本地运行：
```bash
cd deploy
./package.sh
```

## 2. 上传到服务器

```bash
scp cc-term-deploy.tar.gz root@ttyd.ink:/tmp/
```

## 3. 在服务器上部署

```bash
ssh root@ttyd.ink
cd /tmp
tar -xzf cc-term-deploy.tar.gz
cd cc-term-deploy
cp bin/cc-proxy-server.py /opt/cc-term/bin/
cp config/ttyd/index.html /opt/cc-term/config/ttyd/
cd deploy
./setup-server.sh ttyd.ink admin@ttyd.ink
```

## 4. 本地使用

### 方式 A: 使用 Cloudflare Tunnel（推荐）

本地 ttyd 通过 cloudflared 暴露到公网：

```bash
# 安装
brew install cloudflare/cloudflare/cloudflared

# 配置（一次性）
cloudflared tunnel login
cloudflared tunnel create cc-term

# 每次使用前启动隧道
cloudflared tunnel run cc-term &

# 使用远程代理
cc-term main -r
```

### 方式 B: 本地代理模式

如果只在局域网使用：

```bash
# 启动本地代理服务器
cc-term -server &

# 使用本地代理
cc-term main -r --local
```

## 环境变量配置

可以通过环境变量自定义代理服务器：

```bash
export CC_PROXY_HOST=ttyd.ink
export CC_PROXY_PORT=443
export CC_PROXY_PROTOCOL=https

cc-term main -r
```

## 带密码保护

```bash
cc-term main -r -u myuser -p mypass
```

访问时需要输入用户名和密码。
