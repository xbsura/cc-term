# cc-term 远程访问安全机制

本文档说明 cc-term 远程终端访问的安全设计、各层保护机制，以及已知的安全边界。

## 整体架构

```
浏览器 ──(HTTPS)──→ Nginx ──→ cc-proxy-server.py ──(WebSocket)──→ 本地 ttyd
                                      ↑
               cc-tunnel-client ──(WebSocket 反向隧道)──┘
```

## 安全层次

### 1. Session 注册认证（agg_key / agg_secret）

每个客户端首次连接代理服务器时，会通过 `POST /api/agg/new` 获取一对 `agg_key` / `agg_secret` 凭证，保存在 `~/.cc-term/run/agg_credentials.json`。

- 注册新 session（`POST /api/register`）**必须提供有效的 agg_key/agg_secret**
- 服务端严格校验：`agg_keys[agg_key] == agg_secret`，不匹配返回 `403 Forbidden`
- 这保证了只有获得过凭证的客户端才能注册 session

### 2. 服务端 Token 限制（可选）

代理服务器支持 `--token` 参数启动：

```bash
cc-proxy-server.py --port 9999 --token my-secret-token
```

设置后，`POST /api/agg/new` 请求必须携带正确的 `server_token` 才能获取 agg 凭证。这提供了一层额外的服务端准入控制。

### 3. Session 访问 Token

每个注册的 session 会获得一个 **24 字节随机十六进制 token**（`openssl rand -hex 12`，即 96 位熵）。

- 访问 session 的 URL 为 `https://domain/t/<token>/`
- Token 是访问 session 的唯一凭据
- 96 位随机性使暴力猜测在计算上不可行

### 4. HTTP Basic Auth（可选，推荐）

注册 session 时可设置用户名和密码：

```bash
cc-term main -r -u admin -p secret
```

- 设置后，浏览器访问 `/t/<token>/` 会触发 HTTP 401 认证弹窗
- 必须输入正确的用户名/密码才能建立 WebSocket 连接
- 认证信息存储在代理服务器内存中，不落盘
- **建议在生产环境中始终启用**，作为 token 之外的���二层防护

### 5. 传输层加密（TLS）

- 默认云代理（ttyd.ink）使用 HTTPS/WSS（443 端口）
- 所有数据（包括 URL 中的 token）在 TLS 加密通道内传输
- 自建服务器应通过 Nginx + Let's Encrypt 配置 SSL

## WebSocket 数据传输

### 隧道控制通道

客户端通过 `cc-tunnel-client.py` 建立持久的 WebSocket 连接到代理服务器：

- 路径：`/api/tunnel?token=<session_token>`
- 30 秒 keepalive ping/pong 保活
- 仅传输控制指令（JSON 格式），如 `{"action": "connect", "conn_id": "..."}`

### 隧道数据通道

当浏览器请求访问某个 session 时：

1. 代理服务器通过控制通道通知客户端开启数据通道
2. 客户端连接 `/api/tunnel/data/<conn_id>`，`conn_id` 是 32 字节随机十六进制
3. 数据通道建立后，进行**原始字节中继**（raw byte relay）
4. 浏览器 ←→ 代理服务器 ←→ 隧道数据通道 ←→ 本地 ttyd

### 本地代理模式

使用 `cc-term -server` 在本地启动代理时，ttyd 绑定在 `localhost` 的动态端口（17681+），代理服务器直接通过本地 TCP 连接到 ttyd，不经过隧道。

## 已知的安全边界

### Token 泄露风险

- Token 出现在 URL 路径中。在 HTTPS 下，URL 路径被 TLS 加密，不会在网络上泄露
- 但 token 可能出现在：浏览器历史记录、服务器访问日志、Referer 头部
- **缓解**：始终启用 Basic Auth 作为二次验证

### WebSocket 无逐帧认证

- WebSocket 连接建立后，数据通道是纯粹的字节转发，不对每一帧做身份校验
- 安全性完全依赖于：(1) token 的不可猜测性，(2) TLS 加密，(3) 可选的 Basic Auth
- 这是 WebSocket 代理的标准做法，与 Cloudflare Tunnel、ngrok 等工具的安全模型一致

### conn_id 窗口期

- 数据通道连接使用随机 `conn_id`（128 位熵），有效期仅 10 秒
- 攻击者需要在 10 秒内猜中 128 位随机值才能劫持连接，概率可忽略

### 内存存储

- Session 信息、认证凭据存储在代理服务器进程内存中
- 服务器重启后所有 session 注册失效，需要客户端重新注册
- 不存在数据库泄露风险

## 安全建议

1. **始终使用 HTTPS** — 自建服务器必须配置 SSL，防止 token 在网络上明文传输
2. **启用 Basic Auth** — 使用 `-u`/`-p` 参数为 session 设置密码保护
3. **使用 server token** — 自建服务器使用 `--token` 参数限制注册权限
4. **标记私有 session** — 使用 `-s` 参数隐藏 session，不在聚合页面显示
5. **定期轮换** — 重启代理服务器会清除所有 session，相当于强制轮换所有 token
