# 部署 cc-term 到 ttyd.ink

## 架构说明

由于本地 ttyd 运行在本地端口，远程代理服务器无法直接访问，需要使用以下方案之一：

### 方案 1: frp 反向代理（推荐）

使用 frp 将本地 ttyd 端口暴露到远程服务器。

#### 服务器端 (ttyd.ink)

1. 安装 frp server:
```bash
wget https://github.com/fatedier/frp/releases/download/v0.52.0/frp_0.52.0_linux_amd64.tar.gz
tar -xzf frp_0.52.0_linux_amd64.tar.gz
cd frp_0.52.0_linux_amd64
```

2. 配置 frps.ini:
```ini
[common]
bind_port = 7000
vhost_http_port = 8080
token = your-secret-token
```

3. 启动 frp server:
```bash
./frps -c frps.ini
```

#### 客户端 (本地 Mac)

1. 安装 frp client:
```bash
brew install frp
```

2. 配置 frpc.ini:
```ini
[common]
server_addr = ttyd.ink
server_port = 7000
token = your-secret-token

[ttyd-{session}]
type = http
local_ip = 127.0.0.1
local_port = 17681
custom_domains = {session}.ttyd.ink
```

### 方案 2: 使用公网 IP

如果本地有公网 IP，可以直接注册本地 ttyd 地址到远程代理。

## 服务器部署步骤

1. 上传文件到服务器:
```bash
scp -r bin config deploy root@ttyd.ink:/tmp/cc-term-deploy/
```

2. SSH 登录服务器:
```bash
ssh root@ttyd.ink
```

3. 运行部署脚本:
```bash
cd /tmp/cc-term-deploy/deploy
chmod +x deploy.sh
./deploy.sh
```

4. 验证服务:
```bash
systemctl status cc-term-proxy
curl https://ttyd.ink
```

## 本地使用

```bash
# 使用远程代理（默认）
cc-term main -r

# 使用本地代理
cc-term main -r --local

# 带密码保护
cc-term main -r -u admin -p secret
```
