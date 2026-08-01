# DataEase & SQLBot 运维速查

> 最后更新: 2026-08-01

---

## 一、DataEase

- **地址**: `http://localhost:8100`
- **账号**: `admin` / `123456`

```bash
# 启动（先确保 mysql-de 已运行）
docker start mysql-de
docker logs mysql-de --tail 3          # 看到 "ready for connections" 后
docker start dataease                   # 等 30-60s 到 healthy

# 停止（释放内存）
docker stop dataease

# 日志
docker logs -f dataease                    # 实时
docker logs dataease --tail 50             # 最近 50 行

# 状态
docker ps --filter name=dataease --format "{{.Status}}"
```

---

## 二、SQLBot（开发模式，docker compose）

- **地址**: `http://localhost:5173`（前端 dev）/ `http://localhost:8000`（后端 API）
- **账号**: `admin` / `SQLBot@123456`

```bash
# 启动（前置：基座镜像已构建）
docker build -f Dockerfile-base -t sqlbot-python-pg:local .
docker compose up -d

# 重启（改依赖后）
docker compose restart sqlbot

# 日志
docker logs -f sqlbot                    # 实时
docker logs sqlbot --tail 50             # 最近 50 行
docker logs sqlbot 2>&1 | grep ERROR     # 只看错误

# 状态
docker ps --filter name=sqlbot --format "{{.Status}}"
```

### 端口映射

| 端口 | 用途 | 说明 |
|------|------|------|
| 8000 | 后端 API | 已映射，浏览器/curl 可访问 |
| 8001 | MCP | 已映射 |
| 5173 | 前端 vite dev | 已映射（开发模式） |
| **5432** | **内置 postgres** | **已映射**（`docker-compose.yml`），宿主机可用数据库工具连 `localhost:5432` |

### 内置 postgres 连接

| 项 | 值 |
|----|-----|
| 地址 | `localhost:5432` |
| 用户 | `root` |
| 密码 | `Password123@pg` |
| 库名 | `sqlbot` |

```bash
docker exec -it sqlbot psql -U root -d sqlbot   # 容器内连
psql -h localhost -U root -d sqlbot             # 宿主机连（需已装 psql）
```

> 数据持久化在宿主机 `data/postgresql/` 目录。

### 账号密码（SQLBot 系统）

| 项 | 值 | 配置位置 |
|----|-----|---------|
| 登录账号 | `admin` | 初始用户 |
| 默认密码 | `SQLBot@123456` | `.env.sample` 的 `DEFAULT_PWD` |
| JWT 密钥 | 见 `.env.sample` `SECRET_KEY` | 生产务必改为随机值 |
| postgres 账号 | `root` / `Password123@pg` | compose 的 `POSTGRES_*` / 内置 |

### 配置外部数据库（系统库）

默认 SQLBot 用内置 postgres 做系统库。要换外部库，编辑根目录 `.env.sample`（复制为 `.env`）：

```env
# 覆盖内置 postgres（支持 MySQL / PostgreSQL）
SQLBOT_DB_URL=mysql+pymysql://root:password@127.0.0.1:3306/sqlbot
SQLBOT_DB_URL=postgresql+psycopg://user:pass@host:5432/db
```

设置后 `config.py` 会用 `SQLBOT_DB_URL` 覆盖 `POSTGRES_*`，重启容器生效。

> ⚠️ **「要分析的数据库」不是在这里配**，而是在 SQLBot 前端「数据源管理」页面新增数据源（MySQL/Oracle/PG/达梦等），填连接信息即可。

---

## 三、改代码后的开发流程

开发模式是**源码挂载 + 热重载**，改代码即时生效，不用重启：

```bash
# 改 backend/*.py → uvicorn --reload 自动重载
# 改 frontend/*.vue → vite HMR 即时生效
# 改依赖（pyproject/package.json）→ docker compose restart sqlbot
```

> 社区版限制：前端 `vue-tsc` 类型检查会失败（企业版类型缺失），compose 已用 `npx vite` 跳过，直接 dev。

---

## 四、生产部署（installer）

见 [installer/README.md](../../installer/README.md) 与 [docs/deployment/docker-build-and-deploy.md](../deployment/docker-build-and-deploy.md)。

```bash
# 在目标机器（离线/在线安装包）
sudo ./install.sh
sctl status / start / stop / restart
```

---

## 相关文档

- [docs/development/quickstart.md](../development/quickstart.md) —— 开发手册（如何启动/跑/配置）
- [docs/deployment/docker-build-and-deploy.md](../deployment/docker-build-and-deploy.md) —— 部署/构建 SSOT
- [installer/README.md](../../installer/README.md) —— installer 使用
