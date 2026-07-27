# SQLBot 接入钉钉 — 可行性分析

## 一、接入全景图

```
┌──────────────────────────────────────────────────────────────────────┐
│                           钉钉客户端                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌───────────────────────────┐   │
│  │ H5 微应用    │  │ 群机器人 @   │  │ 单聊机器人                 │   │
│  │ (登录+问答)  │  │ (群内提问)   │  │ (私聊提问)                 │   │
│  └──┬──────────┘  └──┬──────────┘  └──┬────────────────────────┘   │
└─────┼────────────────┼────────────────┼─────────────────────────────┘
      │                │                │
      │ OAuth 2.0      │ Stream模式      │ Stream模式
      │ 免登code       │ WebSocket推送   │ WebSocket推送
      │                │                │
      ▼                ▼                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        SQLBot 钉钉适配层 (新增)                       │
│                                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────────┐ │
│  │ DingTalk Auth │  │ Stream Callback│  │ Message Dispatcher       │ │
│  │ code→token    │  │ 消息接收/解析  │  │ 文本→Agent / 图表→卡片    │ │
│  │ userId→用户   │  │ 签名验证       │  │                          │ │
│  └──────┬───────┘  └──────┬───────┘  └────────────┬───────────────┘ │
└─────────┼─────────────────┼───────────────────────┼─────────────────┘
          │                 │                       │
          ▼                 ▼                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      SQLBot 现有核心 (不改动)                         │
│                                                                     │
│  TokenMiddleware → Agent(ReAct) → 多DB查询 → 图表生成 → 权限过滤     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 二、登录鉴权：钉钉免登 → SQLBot 用户

### 钉钉侧流程

```
用户打开 H5 微应用
  → dd.requestAuthCode({corpId, clientId})
  → 获得临时 authCode（5分钟有效）
  → 后端 /api/v1/dingtalk/login 接收 code
  → 调用钉钉 /v1.0/oauth2/{corpId}/token 换 access_token
  → 调用 /topapi/v2/user/getuserinfo 获取用户信息
  → 返回 { userId, name, unionId, avatar, mobile }
```

### SQLBot 侧映射

```python
# 新增: backend/apps/system/api/dingtalk.py
@router.post("/dingtalk/login")
async def dingtalk_login(auth_code: str):
    # 1. 钉钉鉴权 → 拿用户信息
    user_info = await dingtalk_get_user_info(auth_code)
    
    # 2. 映射到 SQLBot 用户
    user = session.query(SysUser).filter(
        SysUser.account == f"dd_{user_info.userId}"  # 前缀区分来源
    ).first()
    
    if not user:
        user = SysUser(
            account=f"dd_{user_info.userId}",
            name=user_info.name,
            oid=default_workspace_id,
        )
        session.add(user)
    
    # 3. 签发 SQLBot JWT → 后续请求走现有 TokenMiddleware
    return {"token": create_jwt(user.id)}
```

### 关键：权限隔离完全不受影响

```python
# 现有权限过滤链 — 基于 current_user.id，与登录方式无关
get_column_permission_fields(session, current_user, table, fields)
get_row_permission_filters(session, current_user, ds, tables)
apply_permissions(sql, tables_used, memory, llm)
```

钉钉用户 → SQLBot `sys_user.id=X` → `ds_rules` 绑定权限 → 列级/行级过滤生效。**与用户名密码登录的用户走同一条权限链。**

---

## 三、消息通道：Stream 模式（推荐）

### 为什么选 Stream 模式

| | HTTP Webhook | Stream 模式 |
|---|---|---|
| 公网 IP | 需要 | **不需要** |
| TLS 证书 | 需要 | **不需要** |
| 防火墙 | 需开放端口 | **不需要** |
| 本地开发 | 需内网穿透 | **直接可用** |
| 部署复杂度 | 高 | **低** |

### 实现方案

```python
# 使用 dingtalk-stream 官方 Python SDK
import dingtalk_stream

class SQLBotCallbackHandler(dingtalk_stream.ChatbotHandler):
    async def process(self, callback):
        # 1. 接收消息
        text = callback.text          # 用户问题
        sender_id = callback.sender_id  # 钉钉用户ID
        
        # 2. 映射到 SQLBot 用户
        user = get_or_create_sqlbot_user(sender_id)
        
        # 3. 调用 Agent 引擎（异步 SSE 流式）
        async for event in sqlbot_agent_stream(user, text):
            if event.type == "text-delta":
                await self.send_text(event.content)
            elif event.type == "chart":
                # ★ 图表渲染 → 钉钉卡片或图片
                image_url = await render_chart_to_image(event.chart_config)
                await self.send_image(image_url)
            elif event.type == "sql":
                await self.send_markdown(f"```sql\n{event.sql}\n```")
```

### 消息回复方式

| 场景 | 钉钉消息类型 | 实现 |
|------|------------|------|
| Agent 文本回答 | Markdown / 文本消息 | 原样推送 |
| SQL 展示 | Markdown 代码块 | ` ```sql ... ``` ` |
| 图表（表格） | Markdown 表格 或 AI 卡片 | 小表格用 Markdown |
| 图表（柱/折/饼） | **图片消息** | g2-ssr → PNG → 钉钉媒体上传 |
| 错误提示 | 文本消息 | 错误信息 + 重试引导 |

---

## 四、图表渲染：三种方案

### 方案 A：图片（推荐，兼容性最好）

```
Agent 生成图表 JSON
  → g2-ssr (Node.js, 已有) 渲染为 PNG
  → 上传至钉钉媒体接口 /media/upload
  → 发送图片消息
```

**优点**：所有图表类型支持，效果与 Web 端一致
**缺点**：图片不可交互

### 方案 B：AI 卡片流式输出

```
Agent 生成结果
  → 构建钉钉 AI 卡片 (YAML/JSON 模板)
  → 通过 sessionWebhook 发送
  → 支持流式打字机效果
```

**优点**：流式体验好，支持交互按钮
**缺点**：卡片模板只能做表格型展示，复杂图表表达有限

### 方案 C：混合（表格用卡片，图表用图片）

```
查询结果 → 表格 → AI 卡片（可交互排序）
         → 柱/折/饼图 → PNG 图片
         → 一条消息同时包含文字 + 表格 + 图片
```

**推荐方案 C**，也是 Dify 等同类产品的做法。

---

## 五、权限隔离验证路径

```
钉钉组织架构                    SQLBot 权限映射
┌─────────────────┐           ┌──────────────────────┐
│ 人事行政部        │           │ ds_rules: 人事规则     │
│  ├─ 张三(userA)  │──免登映射──→│  ├─ userA (dd_userA)  │
│  └─ 李四(userB)  │           │  └─ userB (dd_userB)  │
├─────────────────┤           ├──────────────────────┤
│ 仓储物流部        │           │ ds_rules: 仓管规则     │
│  └─ 王五(userC)  │──免登映射──→│  └─ userC (dd_userC)  │
└─────────────────┘           └──────────────────────┘

张三@机器人 "有哪些用户？"
  → current_user = userA
  → get_column_permission_fields() → 字段不被禁用
  → 生成的 SQL 包含 Level, power_level, Password 等字段
  → Agent 返回完整结果

王五@机器人 "有哪些用户？"
  → current_user = userC
  → 仓管规则 → Password, Level, power_level 被禁用
  → Agent 拿到的字段列表不含这些 → SQL 不查
  → 权限隔离生效 ✅
```

---

## 六、需要新增的代码量估算

| 模块 | 文件 | 行数 | 说明 |
|------|------|:---:|------|
| 钉钉鉴权 | `backend/apps/system/api/dingtalk.py` | ~80 | OAuth 回调 + 用户映射 |
| Stream 回调 | `backend/apps/dingtalk/stream_handler.py` | ~150 | 消息接收 + Agent 调度 |
| 消息回复 | `backend/apps/dingtalk/message_sender.py` | ~100 | 文本/Markdown/图片/卡片 |
| 图表渲染 | 复用 `g2-ssr/` | ~20 | 加一个钉钉导出适配 |
| 依赖 | `pyproject.toml` | ~5 | `dingtalk-stream` 包 |
| **总计** | | **~350 行** | 不改动现有核心逻辑 |

---

## 七、结论

| 问题 | 答案 |
|------|------|
| 钉钉登录令牌能否共用？ | ✅ OAuth code → SQLBot JWT，现有 TokenMiddleware 无需改动 |
| 权限隔离是否支持？ | ✅ 用户映射到 sys_user.id，现有 ds_rules + 列/行权限全部生效 |
| 图表渲染是否支持？ | ✅ g2-ssr → PNG 图片，或钉钉 AI 卡片 |
| 需要改现有核心代码吗？ | ❌ 不需要，全部通过新增适配层实现 |
| 部署需要公网 IP 吗？ | ❌ Stream 模式不需要，WebSocket 反向连接 |

**核心原则**：钉钉只做"通道"和"身份"——负责把用户是谁、问了什么传进来，把 SQLBot 的回答送出去。权限、SQL 生成、图表渲染全部复用现有能力。
