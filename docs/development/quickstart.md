# SQLBot 开发手册（Quickstart）

> 状态：Current
> 最后验证：2026-08-08
> 适用范围：本地与 Docker 开发环境。

> 面向**开发者**：怎么把 SQLBot 在本地跑起来、怎么改代码、怎么调试。
> 部署/构建细节见 [docs/deployment/docker-build-and-deploy.md](../deployment/docker-build-and-deploy.md)；运维见 [docs/operations/DataEase_SQLBot_运维手册.md](../operations/DataEase_SQLBot_运维手册.md)。

---

## 一、环境要求

| 项 | 要求 |
|----|------|
| Python | 3.11（后端） |
| Node.js | 18+（前端） |
| 包管理 | uv（后端）、npm（前端） |
| 数据库 | 可选：本地跑需自备 PostgreSQL 15+；Docker 跑则内置 |
| Docker | 可选（Docker 开发模式） |

---

## 二、方式 A：宿主机本地跑（不依赖 Docker）

适合：只想快速改后端/前端，不想要容器环境。

### 1. 后端

```bash
cd backend
uv sync --extra cpu          # 安装依赖（CPU 版 PyTorch，首次会下载几 GB）
uv run uvicorn main:app --port 8000 --reload   # 启动，热重载
```

> 需要 PostgreSQL：配置根目录 `.env`（后端从 `backend/` 上一级读 `.env`）。
> ```env
> POSTGRES_SERVER=localhost
> POSTGRES_PORT=5432
> POSTGRES_USER=root
> POSTGRES_PASSWORD=你的密码
> POSTGRES_DB=sqlbot
> # 或用 SQLBOT_DB_URL 整体覆盖（支持 MySQL）
> # SQLBOT_DB_URL=mysql+pymysql://root:pass@127.0.0.1:3306/sqlbot
> ```

### 2. 前端

```bash
cd frontend
npm install
npm run dev    # :5173，VITE_API_BASE_URL 指向 http://localhost:8000/api/v1（见 frontend/.env.development）
```

> ⚠️ **社区版限制**：`npm run dev` 里含 `vue-tsc -b` 类型检查会失败（企业版类型缺失）。改跑 `npx vite`（跳过检查）：
> ```bash
> npx vite --host 0.0.0.0
> ```

---

## 三、方式 B：Docker 开发模式（推荐，源码挂载 + 热重载）

适合：想要完整环境（含 postgres + 各种数据库驱动），改代码即时生效。

### 前置：构建基座镜像（一次）

```bash
docker build -f Dockerfile-base -t sqlbot-python-pg:local .
```
> 基座含 postgres 17 + python 3.11 + node 18 + oracle/dm 驱动，构建一次长期复用。

### 启动

```bash
docker compose up -d
```

| 端口 | 服务 |
|------|------|
| 8000 | 后端 API（uvicorn --reload） |
| 8001 | MCP 端口映射；默认 Compose 未启动 `mcp_app` |
| 5173 | 前端 vite dev |
| 5432 | 内置 postgres（宿主机可连 localhost:5432） |

> 💡 **为什么开发用 5173 访问前端，而生产用 8000？**
>
> - **开发模式**：前端用 **vite dev server**（`:5173`）跑源码，改 `.vue` 即时热重载（HMR）。所以开发访问 `http://localhost:5173`。
> - **生产（installer 离线包）**：前端 `npm run build` 成 `dist/` 静态文件，由**后端 FastAPI 托管**在 8000 端口。所以生产访问 `http://IP:8000`。
> - 与 nginx 无关——8000 端口上是 FastAPI（uvicorn），不是 nginx。
> - 开发模式不用 8000 托管前端，是因为要保留热重载；且社区版 `vite build` 会因类型检查失败（见下方社区版限制）。

### 改代码

```bash
# 改 backend/*.py → uvicorn --reload 自动重载
# 改 frontend/*.vue → vite HMR 即时生效
# 改依赖（pyproject/package.json）→ docker compose restart sqlbot
```

### 常用命令

```bash
docker compose logs -f sqlbot   # 日志
docker compose restart sqlbot    # 重启（改依赖后）
docker compose down              # 停止
docker exec -it sqlbot psql -U root -d sqlbot   # 连内置 postgres
```

---

## 四、账号密码

| 项 | 值 | 位置 |
|----|-----|------|
| SQLBot 登录 | `admin` / `SQLBot@123456` | 初始用户 |
| postgres（Docker） | `root` / `Password123@pg` / 库 `sqlbot` | `docker-compose.yml` 的 POSTGRES_* |
| JWT 密钥 | `.env` 的 `SECRET_KEY` | 生产必改 |

> ⚠️ 默认密码是弱密码，生产部署务必改 `installer/install.conf` 与 `.env` 的 `SECRET_KEY`。

---

## 五、配置外部数据库（要分析的业务库）

**这是和系统库不同的概念**：

- **系统库**（SQLBot 自己的数据）：默认内置 postgres / 本地 PG。换外部库 → `.env` 设 `SQLBOT_DB_URL`
- **业务数据源**（用户提问要分析的库）：在 **SQLBot 前端「数据源管理」页面**新增数据源（MySQL/Oracle/PG/达梦等），填连接信息。SQLBot 用它探索表、生成 SQL 执行。

---

## 六、测试与检查

```bash
cd backend
uv run pytest ../tests/ -v   # 全部测试
uv run ruff check .          # 代码检查
uv run mypy apps/            # 类型检查
```

---

## 七、常见问题

| 问题 | 解决 |
|------|------|
| `uvicorn: not found` | 用 `uv run uvicorn`（uv 环境），不要裸 `uvicorn` |
| `sqlbot-xpack` 下载超时 | `uv sync` 前设 `UV_HTTP_TIMEOUT=600`（test.pypi 慢） |
| 前端 vue-tsc 报错 | 社区版限制，用 `npx vite` 跳过 |
| `ModuleNotFoundError: common.utils.dict_to_xml` | 缺文件（社区版缺陷），需 `backend/common/utils/dict_to_xml.py` |
| 后端启动失败 mcp 相关 | `pyproject.toml` 需 `mcp>=1.8.1,<2.0.0`（mcp 2.0 不兼容 fastapi-mcp） |
| curl 登录报 base64 错 | 正常——登录凭据前端加密，curl 传明文会报错 |

---

## 相关文档

- [docs/deployment/docker-build-and-deploy.md](../deployment/docker-build-and-deploy.md) —— 部署/构建 SSOT
- [docs/operations/DataEase_SQLBot_运维手册.md](../operations/DataEase_SQLBot_运维手册.md) —— 运维速查
- [docs/development/agent-maintenance.md](agent-maintenance.md) —— Agent 系统维护
