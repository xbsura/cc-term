# 部署 cc-term 代理服务器

## 快速部署

```bash
# 1. 上传文件到服务器
scp bin/cc-proxy-server.py root@your-server:/tmp/
scp -r config/ttyd root@your-server:/tmp/

# 2. SSH 到服务器运行部署脚本
ssh root@your-server
bash /tmp/setup-server.sh your-domain.com admin@your-domain.com
```

详细步骤请参阅 [DEPLOY_GUIDE.md](DEPLOY_GUIDE.md)。

## 文件说明

| 文件 | 说明 |
|------|------|
| `setup-server.sh` | 一键部署脚本（安装依赖、配置 systemd、Nginx、SSL） |
| `deploy.sh` | 部署脚本（手动步骤版本） |
| `nginx.conf` | Nginx 配置模板 |
| `package.sh` | 打包部署文件 |
| `publish.sh` | 发布脚本 |

## 服务器端架构

```
cc-proxy-server.py (port 9999)
  ├── 接收客户端 WebSocket 反向隧道连接
  ├── 管理 session 注册（agg_key/agg_secret 认证）
  ├── 代理浏览器 WebSocket 到本地 ttyd
  └── 提供 session 聚合页面
          ↕
Nginx (port 443, SSL)
  └── 反向代理所有请求到 9999
```

## 客户端使用

```bash
# 使用默认代理 (ttyd.ink)
cc-term main -r

# 使用自建代理
CC_PROXY_HOST=your-domain.com cc-term main -r

# 带密码保护
cc-term main -r -u admin -p secret
```
