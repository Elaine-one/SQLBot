# SQLBot Installer（离线/在线安装包）

> 状态：Current
> 最后验证：2026-08-08
> 适用范围：生产安装、升级与卸载。

> 本文档说明 `installer/` 目录的用途、安装流程、配置项与升级/卸载。
> 面向**部署运维**；开发模式见 `../docs/deployment/docker-build-and-deploy.md`。

## 一、这个目录是干什么的

`installer/` 用于**生产部署**——打包并安装 SQLBot 到目标机器（通常内网/无源码环境）。部署机**没有源码**，只通过镜像 tar.gz 运行。

```
installer/
├── install.sh                 # 一键安装：装 docker → 加载镜像 → 起服务
├── uninstall.sh               # 卸载（删除运行目录/数据/镜像）
├── sctl                       # SQLBot 控制命令（status/start/stop/restart/...）
├── install.conf               # 安装配置（端口/数据库/密钥等）
├── sqlbot/
│   ├── docker-compose.yml     # 部署 compose（image: dataease/sqlbot:SQLBOT_TAG）
│   └── templates/sqlbot.conf  # compose 的 env 模板（envsubst 生成）
├── Dockerfile                 # 应用镜像（含源码，构建 sqlbot:<tag>）
└── start.sh                   # 应用容器入口（postgres → g2-ssr → mcp → main）
```

## 二、安装流程

安装包由 CI（`.github/workflows/package_and_push.yml`）构建，或手动构建（见 §六）。

```bash
# 在目标机器解压安装包
tar -xzf sqlbot-offline-installer-*.tar.gz
cd sqlbot-offline-installer-*

# 编辑配置（端口/数据库/密钥等）
vim install.conf

# 一键安装
sudo ./install.sh
```

`install.sh` 会：
1. 检查/升级环境（已有安装则升级）
2. 复制安装文件到运行目录 `/opt/sqlbot/`
3. 生成配置（`envsubst` 填充 `sqlbot.conf`）
4. 安装 docker + docker-compose（离线包自带，或在线下载）
5. `docker load` 离线包镜像（`load_images()`）
6. `sctl reload` 启动服务

## 三、sctl 控制命令

```bash
sctl status        # 查看服务状态（含健康检查）
sctl start         # 启动
sctl stop          # 停止
sctl restart       # 重启
sctl version       # 查看当前版本（解析 compose 的 image 版本号）
sctl clear-images  # 清理旧版本镜像
sctl clear-logs    # 清理日志
```

> 注意：`sctl` 依赖 `docker-compose.yml` 的 `image: dataease/sqlbot:<tag>` 行解析版本号（`sctl:73`）。**不要**把 compose 改成 `build:`——目标机无源码上下文，`build:` 无法离线工作，且会破坏 `sctl version`/`clear-images`。

## 四、配置项（install.conf）

| 变量 | 说明 |
|------|------|
| `SQLBOT_BASE` | 安装目录（默认 `/opt`） |
| `SQLBOT_WEB_PORT` / `SQLBOT_MCP_PORT` | Web / MCP 端口（默认 8000 / 8001） |
| `SQLBOT_EXTERNAL_DB` | 是否用外部数据库（`false`=内置 postgres，`true`=外部） |
| `SQLBOT_DB_HOST/PORT/DB/USER/PASSWORD` | 数据库连接（外部库时生效） |
| `SQLBOT_DEFAULT_PWD` | 普通用户默认密码 |
| `SQLBOT_SECRET_KEY` | JWT 签名密钥（生产务必改） |
| `SQLBOT_CORS_ORIGINS` | 跨域白名单 |
| `SQLBOT_LOG_LEVEL` | 日志级别 |
| `SQLBOT_CACHE_TYPE` | 缓存类型（memory/redis/None） |
| `SQLBOT_SERVER_IMAGE_HOST` | MCP 图片访问地址 |
| `SQLBOT_ACCESS_TOKEN_EXPIRE_MINUTES` | Token 过期（默认 11520 = 8 天） |
| `SQLBOT_CONTEXT_PATH` | 子路径部署（如 `/sqlbot`） |

## 五、升级

重新跑 `./install.sh`（检测到已安装则走升级分支）：
- 停止旧服务 → 更新运行目录 → `sctl reload`
- 镜像：新版本离线包 `docker load` 会覆盖
- 数据卷（`data/postgresql`、`data/sqlbot/*`）**保留**，不丢失

## 六、手动构建离线包（本地）

离线包由 CI 自动构建（`package_and_push.yml`：本地 `installer/Dockerfile` 构建应用镜像 → `docker save` → 打 tar.gz）。手动构建：

```bash
# 1. 构建基座镜像（前置）
docker build -f Dockerfile-base -t sqlbot-python-pg:local .

# 2. 构建应用镜像（含源码）
docker build -f installer/Dockerfile -t registry.example.com/dataease/sqlbot:v0.9.5 .

# 3. save 成 tar.gz
mkdir -p installer/images
docker save registry.example.com/dataease/sqlbot:v0.9.5 | gzip > installer/images/sqlbot:v0.9.5.tar.gz
```

> 镜像名必须带 `dataease/sqlbot:` 前缀 + 版本（`sctl` 版本解析依赖）。打包时把 `images/`、`docker/` 一起打进安装包 tar.gz。

## 七、卸载

```bash
sudo ./uninstall.sh   # 删除运行目录、数据、镜像（有确认提示）
```

## 相关文档

- [docs/development/quickstart.md](../docs/development/quickstart.md) —— 开发手册（如何启动/跑）
- [docs/deployment/docker-build-and-deploy.md](../docs/deployment/docker-build-and-deploy.md) —— 部署/构建 SSOT（基座、应用镜像、CI 流程）
- [docs/operations/DataEase_SQLBot_运维手册.md](../docs/operations/DataEase_SQLBot_运维手册.md) —— 运维速查
