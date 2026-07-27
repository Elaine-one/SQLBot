# SQLBot 钉钉接入 — 个人单聊机器人

## 交互模式

```
钉钉工作台
  └─ 员工A 打开 SQLBot 应用 → 进入单聊
        │
        │ "有哪些用户？"
        ▼
      ┌──────────┐
      │  SQLBot  │ ← 每条消息自带 sender_id（钉钉userId）
      │  机器人   │   → 映射到 SQLBot 用户 → 权限隔离
      └────┬─────┘
           │
           │ "仓储物流部 41人，财务部 8人……"
           │ [柱状图.png]
           ▼
      员工A 看到结果（只能看他被授权看到的数据）
```

**优势**：
- 天然身份绑定 — 钉钉消息携带 `sender_id`，直接对应 SQLBot 用户
- 权限隔离透明 — A 看到的数据、B 看到的数据，自动按 `ds_rules` 区分
- 不污染群聊 — 个人对话，不打扰其他人
- 部署简单 — Stream 模式，不要公网 IP，不要证书

---

## 第一步：钉钉开放平台创建应用

登录 [open.dingtalk.com](https://open.dingtalk.com) → 应用开发 → 企业内部应用 → 创建应用。

```
┌────────────────────────────────────┐
│ 创建企业内部应用                     │
│                                    │
│ 应用名称: SQLBot                    │
│ 应用描述: 智能数据分析助手            │
│ 应用图标: (上传)                     │
│                                    │
│ [ 确定创建 ]                        │
└────────────────────────────────────┘
```

创建完后记录：

| 位置 | 字段 | 值（示例） |
|------|------|----------|
| 凭证与基础信息 | ClientID | `dingxxxxxxxxxxxxx` |
| 凭证与基础信息 | ClientSecret | `xxxx...` |
| 应用信息 | CorpId | `dingxxxxxxxxxxxxxxxxxxxx` |

---

## 第二步：添加机器人能力（单聊场景）

应用详情页 → 应用能力 → 添加 → **机器人**。

消息接收模式选 **Stream 模式**。

```
┌─────────────────────────────────────────┐
│ 机器人配置                                │
│                                         │
│ 机器人简介: 企业数据分析助手                │
│                                         │
│ ● Stream 模式  ← 选这个！                 │
│   （WebSocket长连接，无需公网IP）           │
│                                         │
│ ○ HTTP 模式                              │
│                                         │
│ [ 保存 ]                                 │
└─────────────────────────────────────────┘
```

---

## 第三步：配置可见范围与权限

### 可见范围

版本管理与发布 → 设置可见范围 → 选择部门或人员。

**这里决定了谁能看到这个机器人** — 选全员或指定部门。

### 权限申请

开发配置 → 权限管理 → 申请：

| 权限 | 用途 |
|------|------|
| 企业内机器人发送消息权限 | 回复单聊消息 |
| 根据 userId 查询用户信息 | 拿用户姓名/头像 |

单聊场景**不需要**群聊权限、不需要卡片权限。两个权限就够了。

---

## 第四步：SQLBot 新增代码

### 4.1 安装

```bash
cd backend
uv add dingtalk-stream
```

### 4.2 `.env`

```bash
DINGTALK_ENABLED=true
DINGTALK_CLIENT_ID=dingxxxxxxxxxxxxx
DINGTALK_CLIENT_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
DINGTALK_CORP_ID=dingxxxxxxxxxxxxxxxxxxxx
```

### 4.3 新增文件结构

```
backend/apps/dingtalk/
├── __init__.py
├── settings.py          # 配置读取
├── stream_handler.py    # Stream 消息处理（核心）
├── auth.py              # 钉钉用户 ↔ SQLBot 用户映射
└── message_sender.py    # 回复消息（文本+图片）
```

### 4.4 `settings.py`

```python
from pydantic import BaseSettings

class DingTalkSettings(BaseSettings):
    DINGTALK_ENABLED: bool = False
    DINGTALK_CLIENT_ID: str = ""
    DINGTALK_CLIENT_SECRET: str = ""
    DINGTALK_CORP_ID: str = ""

    model_config = {"env_file": "../.env", "extra": "ignore"}

dingtalk = DingTalkSettings()
```

### 4.5 `auth.py` — 用户映射（权限隔离的关键）

```python
from sqlmodel import Session, select
from apps.system.models.user_model import SysUser
from common.core.db import engine

DD_PREFIX = "dd_"


def get_or_create_user(dd_user_id: str) -> SysUser:
    """钉钉 userId → SQLBot 内部用户"""
    account = f"{DD_PREFIX}{dd_user_id}"

    with Session(engine) as session:
        user = session.exec(
            select(SysUser).where(SysUser.account == account)
        ).first()

        if not user:
            user = SysUser(
                account=account,
                name=dd_user_id,  # 后续可用 DingTalk API 拿真实姓名
                oid=1,            # 默认工作空间
                status=1,
            )
            session.add(user)
            session.commit()
            session.refresh(user)

    return user
```

**这就是权限隔离的锚点**：钉钉用户 → `sys_user.id` → `ds_rules` 匹配 → 列/行权限生效。

### 4.6 `stream_handler.py` — 核心

```python
from dingtalk_stream import AckMessage
import dingtalk_stream

from apps.dingtalk.auth import get_or_create_user
from apps.dingtalk.message_sender import reply_text, reply_image


class SQLBotHandler(dingtalk_stream.ChatbotHandler):
    """个人单聊消息 → SQLBot Agent → 回复"""

    async def process(self, callback: dingtalk_stream.CallbackMessage):
        msg = dingtalk_stream.ChatbotMessage.from_dict(callback.data)

        # 只处理文本消息
        if msg.message_type != "text":
            return AckMessage.STATUS_OK, "OK"

        text = msg.text.content.strip()
        if not text:
            return AckMessage.STATUS_OK, "OK"

        # 1. 身份映射
        user = get_or_create_user(msg.sender_id)

        # 2. 调用 SQLBot Agent（复用现有引擎）
        try:
            result = await run_sqlbot_agent(
                user=user,
                question=text,
                on_text=lambda t: reply_text(msg, t),
                on_chart=lambda img: reply_image(msg, img),
            )
        except Exception as e:
            reply_text(msg, f"查询出错了：{e}")

        return AckMessage.STATUS_OK, "OK"


def start():
    """启动 Stream 客户端（在 main.py lifespan 中调用）"""
    from apps.dingtalk.settings import dingtalk

    if not dingtalk.DINGTALK_ENABLED:
        return

    credential = dingtalk_stream.Credential(
        dingtalk.DINGTALK_CLIENT_ID,
        dingtalk.DINGTALK_CLIENT_SECRET,
    )
    client = dingtalk_stream.DingTalkStreamClient(credential)
    client.register_callback_handler(
        dingtalk_stream.chatbot.ChatbotMessage.TOPIC,
        SQLBotHandler(),
    )
    # start_forever 是阻塞的，需要放到线程/协程里
    import asyncio
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, client.start_forever)
```

### 4.7 `message_sender.py` — 回复

```python
def reply_text(msg, content: str):
    """回复纯文本"""
    # 通过 robotCode + userId 调钉钉单聊发消息 API
    # POST /v1.0/robot/oToMessages/batchSend
    pass


def reply_image(msg, image_bytes: bytes):
    """回复图表图片"""
    # 1. POST /v1.0/media/upload → media_id
    # 2. POST 单聊图片消息
    pass
```

### 4.8 `main.py` — lifespan 中启动

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... 现有启动逻辑 ...
    from apps.dingtalk.stream_handler import start
    start()
    yield
```

---

## 第五步：发布应用

钉钉开放平台 → 版本管理与发布 → 创建新版本 → 发布。

版本发布后，**可见范围内的员工**在钉钉客户端 → 工作台 → 搜索 SQLBot → 点击 → 进入个人单聊。

---

## 代码量

| 文件 | 行数 | 说明 |
|------|:---:|------|
| `settings.py` | ~12 | 配置读取 |
| `auth.py` | ~25 | 用户映射 |
| `stream_handler.py` | ~60 | 消息→Agent→回复 |
| `message_sender.py` | ~50 | 钉钉 API 调用封装 |
| `.env` 新增 | 4 行 | |
| `main.py` 改动 | 2 行 | lifespan 启动 |
| **总计** | **~150 行** | 不改核心代码 |

---

## 权限隔离验证路径

```
钉钉                                        SQLBot
┌──────────────────┐                   ┌──────────────────────┐
│ 人事部 员工A      │                   │ sys_user: dd_userA   │
│ 打开SQLBot单聊    │                   │   → ds_rules: 人事规则 │
│ 问"有哪些用户？"   │──Stream推送────→  │   → 字段不被禁用       │
│                  │                   │   → SQL 包含完整字段   │
│ 看到: Level,      │←────文本回复────  │   → 查BO_DB，61条     │
│  power_level等    │                   │                      │
└──────────────────┘                   └──────────────────────┘

┌──────────────────┐                   ┌──────────────────────┐
│ 仓储部 员工C      │                   │ sys_user: dd_userC   │
│ 打开SQLBot单聊    │                   │   → ds_rules: 仓管规则 │
│ 问"有哪些用户？"   │──Stream推送────→  │   → Password等被禁用   │
│                  │                   │   → SQL 不含敏感字段   │
│ 看到: 基本字段     │←────文本回复────  │   → 查BO_DB，61条     │
│ 看不到:Password等  │                   │                      │
└──────────────────┘                   └──────────────────────┘
```

**每个钉钉用户一个独立的单聊会话，身份通过 `sender_id` 自动绑定，权限隔离完全透明。**
