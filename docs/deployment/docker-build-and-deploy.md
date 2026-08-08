# SQLBot 部署与构建

> 状态：Current
> 最后验证：2026-08-08
> 适用范围：Docker 构建链、开发镜像、生产镜像和离线安装包。

> 本文档是 SQLBot **Docker 构建链 + 部署形态**的唯一权威说明（SSOT）。对部署/构建的任何改动，请先读本文档并在此更新。
>
> 最后更新: 2026-08-01

---

## 1. 背景：为什么要改构建链

SQLBot 的构建链原本完全依赖上游 dataease 发布的镜像。本地仓库改了大量代码（Agent 引擎、图表系统、前端、工具集等），但这些改动**不在上游发布镜像里**：

- **installer 安装包**（`installer/sqlbot/docker-compose.yml`）直接 `image: registry.cn-qingdao.aliyuncs.com/dataease/sqlbot:SQLBOT_TAG`，用的是上游发布镜像 → 本地代码改动无法生效
- 上游发布镜像由**主 `Dockerfile`** 构建，但其 builder 阶段仍 FROM 上游基座镜像：
  - `registry.cn-qingdao.aliyuncs.com/dataease/sqlbot-base:latest`（前端构建 / Python 依赖构建 / g2-ssr 构建）
  - `registry.cn-qingdao.aliyuncs.com/dataease/sqlbot-python-pg:latest`（运行时）
  - `ghcr.io/1panel-dev/maxkb-vector-model:v1.0.1`（嵌入模型）

**目标**：构建完全基于本地源码（`COPY backend / frontend / g2-ssr`），除 postgres 基座与嵌入模型这两个「无本地源码可 COPY 的环境镜像」外，不再依赖 dataease 发布的 `sqlbot-base` / `sqlbot-python-pg` 镜像。

---

## 2. 镜像构建链（现状 → 目标）

### 现状：上游依赖

```
ghcr.io/1panel-dev/maxkb-vector-model:v1.0.1 ──────────────┐  (嵌入模型)
registry.cn-qingdao.aliyuncs.com/dataease/sqlbot-base:latest ┐ (builder 基座, 无 workflow 构建它)
registry.cn-qingdao.aliyuncs.com/dataease/sqlbot-python-pg:latest ┐ (运行时基座)
    ↓ 3 个 builder 阶段 + 1 个 runtime 阶段
主 Dockerfile ──> dataease/sqlbot:<tag>
```

### 目标：本地构建（仅一个基座）

```
Dockerfile-base ──本地构建──> sqlbot-python-pg:local   (唯一基座, FROM dataease/postgres:17.6)
     ↑ 供 开发模式（compose 源码挂载） 与 应用镜像构建（installer/Dockerfile） 共用
ghcr.io/1panel-dev/maxkb-vector-model:v1.0.1 ──────────────┐ (嵌入模型, 仅应用镜像)
开发模式嵌入模型: 运行期 HuggingFaceEmbeddings 从 hf-mirror 下载 shibing624/text2vec-base-chinese (见 §8.1)
    ↓
开发模式:  docker-compose.yml ──基座+源码挂载──> sqlbot 容器（uvicorn --reload + vite HMR）
离线包:    installer/Dockerfile ──FROM 基座 + COPY 源码──> dataease/sqlbot:<tag> ──docker save──> 离线包
```

> 说明：应用镜像 Dockerfile（`installer/Dockerfile`）不再需要单独 builder 基座——builder 阶段直接复用运行时基座 `sqlbot-python-pg:local`（原 `Dockerfile-base-build` 已删除）。

---

## 3. 基座镜像内容（`Dockerfile-base`）

运行时基座 `sqlbot-python-pg` 是一个 all-in-one 容器，包含：

| 组件 | 来源 | 说明 |
|------|------|------|
| **PostgreSQL 17 + pgvector** | `registry.cn-qingdao.aliyuncs.com/dataease/postgres:17.6` | 唯一外部 FROM。运行时内置 postgres，`start.sh` 用其官方 entrypoint 启动 |
| **Python 3.11**（含 pip/venv） | 从 `python:3.11-slim-bookworm` COPY `/usr/local` | 应用运行时 |
| **uv** | `ghcr.io/astral-sh/uv:0.7.8` 拷二进制 | 构建时用 |
| **node18 + npm** | deb.nodesource + npmjs | g2-ssr SSR + pm2 运行时 |
| **Oracle Instant Client 23.x** | Oracle 官方下载 | 连 Oracle 用 |
| **DM 数据库库** | fit2cloud OSS | 连达梦用 |
| **libcairo/pango/jpeg/gif/svg** | apt | node-canvas（g2-ssr）原生依赖 |
| **xpack license validator** | fit2cloud OSS | 企业版许可 |

> 注意：**基座用 uv，不依赖 venv**。应用虚拟环境由主 Dockerfile 的 `uv sync` 在 `/opt/sqlbot/app/.venv` 创建。

---

## 4. 关键文件职责

| 文件 | 职责 |
|------|------|
| `Dockerfile-base` | **唯一基座** `sqlbot-python-pg:local`（postgres + python + node + oracle/dm + 编译工具）。开发模式与应用镜像共用 |
| `docker-compose.yml`（✅ 唯一 compose，根目录） | **开发模式**：基座镜像 + 源码挂载 + 热重载（`uvicorn --reload` + `vite dev` HMR） |
| `installer/Dockerfile`（✅ 应用镜像，已从根目录移入） | 构建含源码的应用镜像（前端 build + 后端 uv sync + g2-ssr build）。FROM 基座，无独立 builder 基座 |
| `installer/start.sh`（✅ 已从根目录移入） | 应用容器入口：启动 postgres → pm2 起 g2-ssr(:3000) → uvicorn mcp(:8001) → uvicorn main(:8000) |
| `.env.sample`（✅ 根目录） | 环境变量样例（供 compose 用） |
| `backend/uv.lock`（✅ 已生成） | 后端依赖锁文件（261 包）。`installer/Dockerfile` 与开发模式 `uv sync` 的 bind mount 引用 |
| `backend/common/utils/dict_to_xml.py`（✅ 已新增） | sqlbot_xpack 依赖的 dict→XML 工具（基于 dicttoxml 库），缺失会导致后端启动失败 |
| `installer/README.md`（✅ 已新增） | installer 使用文档：安装/配置/sctl/升级/卸载/手动构建离线包 |
| `installer/` | 一键安装：install.sh 装 docker → sctl 拉起来。`installer/sqlbot/docker-compose.yml` 用上游 `image:`（保留） |
| `.github/workflows/build_base_and_push.yml` | CI 构建运行时基座 `sqlbot-python-pg`（可选） |
| `.github/workflows/package_and_push.yml` | 打离线包（✅ 已改：本地 `installer/Dockerfile` 构建 → tag → save，不再 pull 上游） |

---

## 5. 已确认决策

| 项 | 决策 | 理由 |
|----|------|------|
| 基座策略 | **单一基座**，本地构建 | 保留 `Dockerfile-base` → `sqlbot-python-pg:local`，开发模式与应用镜像共用；不建独立 builder 基座 |
| 开发模式 | 唯一 `docker-compose.yml`（源码挂载 + 热重载） | 改源码即时生效，不用重建镜像 |
| 离线包 | 应用镜像 `installer/Dockerfile`（含源码） | 部署机无源码，需 `docker save` 含源码镜像 tar.gz |
| postgres+pgvector | 保留 `dataease/postgres:17.6` | Docker Hub 直连不通（走 mirror 加速源不稳定），不引入 Docker Hub 依赖 |
| 嵌入模型（应用镜像） | 保留 `ghcr.io/1panel-dev/maxkb-vector-model:v1.0.1` | `/opt/sqlbot/models` 是容器内路径，`installer/Dockerfile` 构建时 `COPY --from` 创建。**仅应用镜像/离线包**使用 |
| 嵌入模型（开发模式） | 用 `HuggingFaceEmbeddings` 从 HF 下载 `shibing624/text2vec-base-chinese` | `backend/apps/ai_model/embedding.py` 运行期加载。官方 HF 大陆不可达 → 走 `hf-mirror.com` 镜像 + 挂载 `./data/sqlbot/models`（见 §8.1）。**不走 ghcr 镜像** |
| 依赖锁定 | 生成 `backend/uv.lock` | 让 `installer/Dockerfile` 的 `--mount=type=bind,source=backend/uv.lock` 生效，构建可复现 |
| 国内镜像源 | apt → 阿里云，npm → npmmirror | 加速构建（实测 deb.debian.org 极慢，阿里云源 200） |
| 虚拟环境 | 基座已用 uv（无 venv） | 无需改；只需保证 `uv sync` 的 lockfile/extra 处理正确 |
| `.idea` | 删除 | 仅剩 `icon.png` 被 git 追踪，属清理项 |

---

## 6. 网络可达性（构建前提）

| 目标 | 可达性 | 说明 |
|------|--------|------|
| `registry.cn-qingdao.aliyuncs.com` | ✅ | 基座 postgres + 备用镜像来源 |
| `ghcr.io` | ✅ | uv 二进制 + 嵌入模型 |
| `registry-1.docker.io`（Docker Hub 直连） | ✅（走 mirror） | 直连 curl 000，但 docker daemon 配了 4 个 mirror，`docker pull` 走加速节点 |
| `huggingface.co` | ❌ 000 | 大陆不可达。嵌入模型默认走 ghcr 镜像，但**开发模式**需本地下载 embedding 模型（见 §8，走 `hf-mirror.com` 国内镜像） |
| `pypi.org` / `mirrors.aliyun.com/pypi` | ✅ | 后端依赖源 |
| `test.pypi.org` | ✅ | `sqlbot-xpack` 依赖源（`pyproject.toml:42,84-90`） |
| `npm registry` | ✅ | 前端 / g2-ssr 依赖（均为公网包，无私有源） |
| `download.oracle.com` | ✅ | Oracle Instant Client 下载（实测 200） |
| `resource-fit2cloud-com.oss-cn-hangzhou` | ✅ | DM 库下载（实测 200） |
| `deb.debian.org` | ✅ | 基座 apt 源 |

---

## 7. 本地构建步骤

```bash
# 1. 生成后端依赖锁文件（✅ backend/uv.lock 已生成，已提交）
cd backend && uv lock

# 2. 构建唯一基座 sqlbot-python-pg（✅ 已完成，1.47GB）
docker build -f Dockerfile-base -t sqlbot-python-pg:local .

# 3. 开发模式：源码挂载 + 热重载（改源码即时生效，不用重建镜像）
docker compose up -d

# 4.（可选）应用镜像构建（供离线包）
docker build -f installer/Dockerfile -t sqlbot:local .
```

---

## 8. 开发模式（`docker-compose.yml`，唯一 compose）

```yaml
services:
  sqlbot:
    image: sqlbot-python-pg:local      # 基座镜像
    container_name: sqlbot
    restart: unless-stopped
    privileged: true
    ports:
      - "${SQLBOT_WEB_PORT:-8000}:8000"   # 后端 API
      - "${SQLBOT_MCP_PORT:-8001}:8001"   # MCP
      - "5173:5173"                       # 前端 vite dev
      - "5432:5432"                       # 内置 postgres（宿主机可连）
    working_dir: /opt/sqlbot/app
    command: ...   # 覆盖 start.sh：postgres + uv sync + uvicorn --reload + vite dev（容错）
    environment:
      - POSTGRES_DB=sqlbot
      ...
      # HuggingFace 国内镜像：官方 huggingface.co 大陆不可达，embedding 模型依赖它
      - HF_ENDPOINT=https://hf-mirror.com
    volumes:
      - ./backend:/opt/sqlbot/app          # 源码挂载
      - ./frontend:/opt/sqlbot/frontend
      - ./g2-ssr:/opt/sqlbot/g2-ssr
      - sqlbot-venv:/opt/sqlbot/app/.venv  # .venv/node_modules 匿名卷隔离
      - sqlbot-frontend-node:/opt/sqlbot/frontend/node_modules
      - sqlbot-g2-node:/opt/sqlbot/g2-ssr/node_modules
      - ./data/postgresql:/var/lib/postgresql/data
      # embedding 模型（HF 下载缓存 + SQLBot 期望路径），重启不丢
      - ./data/sqlbot/models:/opt/sqlbot/models
      ...
volumes:
  sqlbot-venv:
  sqlbot-frontend-node:
  sqlbot-g2-node:
```

> 改源码：后端 `.py` → `uvicorn --reload` 自动重载；前端 `.vue` → vite HMR 即时生效。改依赖（pyproject/package.json）才需重启容器。
> 内置 postgres：`data/postgresql` 卷持久化。若用外部数据库，用 `SQLBOT_DB_URL` 覆盖。

### 8.1 embedding 模型（向量模型）国内下载

开发模式用 `HuggingFaceEmbeddings`（`backend/apps/ai_model/embedding.py`）做表/字段语义搜索，模型 `shibing624/text2vec-base-chinese` 需本地存在。官方 huggingface.co 大陆不可达，已配置国内镜像：

- **镜像**：compose `environment` 设 `HF_ENDPOINT=https://hf-mirror.com`，模型下载走 hf-mirror。
- **持久化**：`./data/sqlbot/models:/opt/sqlbot/models` 挂载，模型落盘宿主机，重启不丢。
- **模型路径**（关键）：SQLBot 期望 `/opt/sqlbot/models/embedding/shibing624_text2vec-base-chinese`（`embedding.py:20-22`），而 HF 缓存格式是 `models--shibing624--text2vec-base-chinese`。首次需下载后**跟随 symlink 复制**到期望路径：
  ```bash
  docker exec sqlbot sh -c "cd /opt/sqlbot/app && \
    .venv/bin/python -c \"from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('shibing624/text2vec-base-chinese', cache_folder='/opt/sqlbot/models')\""
  # 复制到 SQLBot 期望路径（HF 缓存用 symlink，必须 -L 跟随）
  docker exec sqlbot sh -c "SNAP=\$(ls -d /opt/sqlbot/models/models--shibing624--text2vec-base-chinese/snapshots/*/ | head -1) && \
    mkdir -p /opt/sqlbot/models/embedding && \
    cp -rL \$SNAP /opt/sqlbot/models/embedding/shibing624_text2vec-base-chinese"
  ```
- **降级行为**：模型缺失/下载失败**不阻塞启动**（`register_all.py:_warm_embedding_model` 的 try/except）。首次使用语义搜索时报 `FileNotFoundError`，表列表降级为不做相关性排序（`table_embedding.py` try/except 返回原始表），问答仍可用但表排序弱化。

---

## 9. installer 离线包改造

- **`installer/sqlbot/docker-compose.yml` 保留 `image:` 行，不改 `build:`** —— install.sh 把 compose 拷到目标机执行，目标机无源码上下文，`build:` 无法离线工作；且 `sctl` 的 `_get_current_version()`（sctl:73）与 `clear_images()`（sctl:135）强依赖 `image:...sqlbot:版本` 解析版本号。去掉 `image:` 会导致 `sctl version`/`clear-images` 失效。
- 真正改造点在 `.github/workflows/package_and_push.yml`：把「`docker pull` 上游发布镜像」改为「本地 `docker build` → 按发布名 `dataease/sqlbot:<tag>` 打 tag → `docker save | gzip` 进 `images/`」。
- `install.sh` 的 `load_images()`（232-254 行）已是 `docker load -i images/*.tar.gz`，无需改动。

---

## 10. CI workflow 处置

| workflow | 职责 | 改造后 |
|----------|------|--------|
| `build_and_push.yml` | 在线构建主镜像 `dataease/sqlbot` | 保留（用 `installer/Dockerfile`，本地源码构建）；需同步 `-f installer/Dockerfile` |
| `build_base_and_push.yml` | 基座 `sqlbot-python-pg` | 保留（`Dockerfile-base`，与本地基座一致） |
| `package_and_push.yml` | 离线包 | ✅ 已改：本地 `installer/Dockerfile` 构建 → tag → save，不再 pull 上游 |
| `xpack.yml` | 企业版 PyPI wheel | 保留（非 Docker 链路） |
| `typos_check.yml` | 拼写检查 | 保留 |
| `sync2gitee.yml` / `sync_to_cnb.yml` | 仓库镜像 | 保留 |

> 不再需要独立 builder 基座（原 `Dockerfile-base-build` 已删）：`installer/Dockerfile` 的 builder 阶段直接复用运行时基座 `sqlbot-python-pg:local`。

---

## 11. 验证方案

1. **基座构建**：`docker build -f Dockerfile-base -t sqlbot-python-pg:local .` 成功（✅ 已完成），容器内 `node --version` / `npm --version` / `psql --version` 可用
2. **开发模式**：`docker compose up -d` → 容器内 postgres + 后端 uvicorn + 前端 vite dev 正常起
   - embedding 模型：`docker exec sqlbot sh -c "cd /opt/sqlbot/app && .venv/bin/python -c 'from apps.ai_model.embedding import EmbeddingModelCache; EmbeddingModelCache.get_model().embed_query(\"测试\")'"` 无报错
3. **端到端**：`docker compose up -d` → `docker logs sqlbot` 无 ERROR → `http://localhost:8000/docs` 有响应（401 鉴权保护正常）→ `http://localhost:5173/` 200 → 浏览器登录（凭据加密由前端处理，curl 传明文会报 base64 错，属正常）
4. **离线包**：按 `installer/README.md` 手动构建流程，`installer/Dockerfile` 构建应用镜像 + docker save + 打 tar.gz，新机器 `install.sh` 安装成功

---

## 12. 风险与待定

1. **`sqlbot-xpack`（test.pypi.org）**：`uv sync` 访问 test.pypi.org 拉取（`pyproject.toml:42,84-90`）。实测可达但慢，需 `UV_HTTP_TIMEOUT` 加大（已处理）；若 CI/内网不可达需改内网 pypi 代理
2. **npm 依赖 `@antv/g2-ssr`**：`^0.1.0` 只能解析到 0.1.x（0.3/0.4 曾被投毒下架，0.1.x 安全）
3. **CI push 目标**：`build_and_push.yml` 用 `installer/Dockerfile` 构建 `dataease/sqlbot`，push 到 dataease 官方 registry（阿里云 ACR + Docker Hub）。若团队只用本地构建，保留 workflow 但改 push 目标为私有 registry
4. **Docker Hub 加速源稳定性**：postgres 基座保留阿里云镜像即避开此风险
5. **`COPY g2-ssr/*.ttf`**：仓库无 .ttf 文件，`installer/Dockerfile` 已改为**条件拷贝**（`if ls g2-ssr/*.ttf` 才拷），不阻塞构建 ✅ 已处理
6. **既有已知项（不在本次范围）**：MCP 图表 PNG 渲染链路接口不匹配——`executor.py:1357` POST `{image_host}/api/chart/render`（payload `{chart, data}`），而 `g2-ssr/app.js:11-21` 只实现 POST `/`（payload `{type, axis, data, path}`）。该调用 best-effort、不阻塞，前端始终用 JSON 渲染图表
7. **登录凭据加密**（正常设计，非故障）：`apps/system/api/login.py:31` 对 username/password 先 `sqlbot_decrypt`，前端提交加密凭据。curl 测登录传明文会报 base64 错误

---

## 相关文档

- [installer 使用文档](../../installer/README.md) —— installer 目录的安装/配置/升级/卸载
- [docs/operations/DataEase_SQLBot_运维手册.md](../operations/DataEase_SQLBot_运维手册.md) —— 启动/停止/日志速查
- [历史部署踩坑记录](../archive/plans/cleanup-lessons-learned.md) —— 仅用于追溯，不代表当前部署流程

---

# 附录 A：本次部署改造发现的运行问题（已修复）

部署验证（`docker compose up -d`）过程中发现并修复了仓库原有、与部署无关的问题，记录如下，避免后人重复排查：

| 问题 | 根因 | 修复 |
|------|------|------|
| 基座缺 npm | `Dockerfile-base` 用 Debian `apt install nodejs`（不含 npm），`curl npmjs/install.sh` 又失败 | 改 `apt-get install -y nodejs npm`，删除失败脚本 |
| `sqlbot-xpack` 下载超时 | test.pypi.org 下载慢，uv 默认 30s 超时 | compose 设 `UV_HTTP_TIMEOUT=600` |
| 前端 vue-tsc 失败 | 社区版类型检查过不了（企业版类型缺失） | 开发模式用 `npx vite` 跳过 vue-tsc |
| 后端启动失败 | 缺 `common/utils/dict_to_xml.py`（sqlbot_xpack 依赖） | 新增该模块（基于 dicttoxml 库） |
| 后端启动失败 | `mcp 2.0` 与 `fastapi-mcp 0.3.x` API 不兼容 | `pyproject.toml` pin `mcp>=1.8.1,<2.0.0`，重新 `uv lock` |
| embedding 模型加载失败 | huggingface.co 大陆不可达，模型未下载 | compose 设 `HF_ENDPOINT=https://hf-mirror.com` + 挂载 `./data/sqlbot/models`，按 §8.1 下载并复制到期望路径 |

> 经验：这些是「部署验证暴露的仓库原有 bug」，本地 `uv run uvicorn` 也会遇到。改完依赖记得重新 `uv lock`。
