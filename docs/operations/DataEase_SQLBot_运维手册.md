# DataEase & SQLBot 运维速查

> 最后更新: 2026-07-15

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

## 二、SQLBot

- **地址**: `http://localhost:8000`
- **账号**: `admin` / `SQLBot@123456`

```bash
# 启动（先确保 my-postgres 已运行）
docker start my-postgres
cd c:\Code\Dataease\SQLBot-main
docker compose up -d

# 重启
docker compose restart sqlbot

# 日志
docker logs -f sqlbot                    # 实时
docker logs sqlbot --tail 50             # 最近 50 行
docker logs sqlbot 2>&1 | findstr ERROR  # 只看错误

# 状态
docker ps --filter name=sqlbot --format "{{.Status}}"
```

---

## 三、前端改代码后重启 SQLBot

```bash
cd c:\Code\Dataease\SQLBot-main\frontend
npx vite build   
npx vite build
                        # 编译前端
cd c:\Code\Dataease\SQLBot-main
docker compose restart sqlbot            # 重启
```
