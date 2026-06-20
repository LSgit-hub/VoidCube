---
name: webhook-subscriptions
description: 创建和管理Webhook订阅以实现事件驱动的Agent激活。当用户希望外部服务自动触发Agent运行时使用。
version: 1.0.0
metadata:
  VoidCube:
    tags: [webhook, events, automation, integrations]
---

# Webhook订阅

创建动态Webhook订阅，使外部服务（GitHub、GitLab、Stripe、CI/CD、IoT传感器、监控工具）可以通过POST事件到URL来触发Voidcube Agent运行。

## 设置（必须先完成）

必须先启用Webhook平台才能创建订阅。检查：
```bash
VoidCube webhook list
```

如果显示"Webhook platform is not enabled"，进行设置：

### 选项1：设置向导
```bash
VoidCube gateway setup
```
按提示启用webhook、设置端口和全局HMAC密钥。

### 选项2：手动配置
添加到 `~/.VoidCube/config.yaml`：
```yaml
platforms:
  webhook:
    enabled: true
    extra:
      host: "0.0.0.0"
      port: 8644
      secret: "generate-a-strong-secret-here"
```

### 选项3：环境变量
添加到 `~/.VoidCube/.env`：
```bash
WEBHOOK_ENABLED=true
WEBHOOK_PORT=8644
WEBHOOK_SECRET=generate-a-strong-secret-here
```

配置后，启动（或重启）网关：
```bash
VoidCube gateway run
# 或使用systemd：
systemctl --user restart VoidCube-gateway
```

验证运行：
```bash
curl http://localhost:8644/health
```

## 命令

所有管理通过 `VoidCube webhook` CLI命令：

### 创建订阅
```bash
VoidCube webhook subscribe <name> \
  --prompt "Prompt template with {payload.fields}" \
  --events "event1,event2" \
  --description "What this does" \
  --skills "skill1,skill2" \
  --deliver telegram \
  --deliver-chat-id "12345" \
  --secret "optional-custom-secret"
```

返回Webhook URL和HMAC密钥。用户配置其服务POST到该URL。

### 列出订阅
```bash
VoidCube webhook list
```

### 移除订阅
```bash
VoidCube webhook remove <name>
```

### 测试订阅
```bash
VoidCube webhook test <name>
VoidCube webhook test <name> --payload '{"key": "value"}'
```

## 提示模板

提示支持 `{dot.notation}` 访问嵌套载荷字段：

- `{issue.title}` — GitHub issue标题
- `{pull_request.user.login}` — PR作者
- `{data.object.amount}` — Stripe支付金额
- `{sensor.temperature}` — IoT传感器读数

如未指定提示，完整JSON载荷将注入Agent提示。

## 常见模式

### GitHub：新issue
```bash
VoidCube webhook subscribe github-issues \
  --events "issues" \
  --prompt "New GitHub issue #{issue.number}: {issue.title}\n\nAction: {action}\nAuthor: {issue.user.login}\nBody:\n{issue.body}\n\nPlease triage this issue." \
  --deliver telegram \
  --deliver-chat-id "-100123456789"
```

然后在GitHub仓库Settings → Webhooks → Add webhook：
- Payload URL：返回的webhook_url
- Content type：application/json
- Secret：返回的secret
- Events："Issues"

### GitHub：PR审阅
```bash
VoidCube webhook subscribe github-prs \
  --events "pull_request" \
  --prompt "PR #{pull_request.number} {action}: {pull_request.title}\nBy: {pull_request.user.login}\nBranch: {pull_request.head.ref}\n\n{pull_request.body}" \
  --skills "github-code-review" \
  --deliver github_comment
```

### Stripe：支付事件
```bash
VoidCube webhook subscribe stripe-payments \
  --events "payment_intent.succeeded,payment_intent.payment_failed" \
  --prompt "Payment {data.object.status}: {data.object.amount} cents from {data.object.receipt_email}" \
  --deliver telegram \
  --deliver-chat-id "-100123456789"
```

### CI/CD：构建通知
```bash
VoidCube webhook subscribe ci-builds \
  --events "pipeline" \
  --prompt "Build {object_attributes.status} on {project.name} branch {object_attributes.ref}\nCommit: {commit.message}" \
  --deliver discord \
  --deliver-chat-id "1234567890"
```

### 通用监控告警
```bash
VoidCube webhook subscribe alerts \
  --prompt "Alert: {alert.name}\nSeverity: {alert.severity}\nMessage: {alert.message}\n\nPlease investigate and suggest remediation." \
  --deliver origin
```

## 安全性

- 每个订阅获得自动生成的HMAC-SHA256密钥（或用 `--secret` 提供自己的）
- Webhook适配器验证每个传入POST的签名
- config.yaml中的静态路由不能被动态订阅覆盖
- 订阅持久化到 `~/.VoidCube/webhook_subscriptions.json`

## 工作原理

1. `VoidCube webhook subscribe` 写入 `~!/.VoidCube/webhook_subscriptions.json`
2. Webhook适配器在每个传入请求时热重载此文件（mtime门控，开销可忽略）
3. 当POST到达匹配路由时，适配器格式化提示并触发Agent运行
4. Agent的响应投递到配置的目标（Telegram、Discord、GitHub评论等）

## 故障排除

如果Webhook不工作：

1. **网关在运行吗？** 用 `systemctl --user status VoidCube-gateway` 或 `ps aux | grep gateway` 检查
2. **Webhook服务器在监听吗？** `curl http://localhost:8644/health` 应返回 `{"status": "ok"}`
3. **检查网关日志：** `grep webhook ~/.VoidCube/logs/gateway.log | tail -20`
4. **签名不匹配？** 验证服务中的密钥与 `VoidCube webhook list` 中的匹配。GitHub发送 `X-Hub-Signature-256`，GitLab发送 `X-Gitlab-Token`。
5. **防火墙/NAT？** Webhook URL必须可从服务访问。本地开发使用隧道（ngrok、cloudflared）。
6. **事件类型错误？** 检查 `--events` 过滤器匹配服务发送的内容。用 `VoidCube webhook test <name>` 验证路由工作。
