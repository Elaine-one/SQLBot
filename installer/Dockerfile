# SQLBot 应用镜像 Dockerfile（供离线包 / 在线构建用）
# 构建：docker build -f installer/Dockerfile -t <tag> .
# context 为仓库根（含 backend/frontend/g2-ssr/start.sh）
# 基于本地基座镜像 sqlbot-python-pg:local（builder 阶段直接复用运行时基座，不再需要单独 builder 基座）

# 前端构建阶段
FROM --platform=${BUILDPLATFORM} sqlbot-python-pg:local AS sqlbot-ui-builder
ENV SQLBOT_HOME=/opt/sqlbot
ENV APP_HOME=${SQLBOT_HOME}/app
ENV UI_HOME=${SQLBOT_HOME}/frontend
ENV DEBIAN_FRONTEND=noninteractive

RUN mkdir -p ${APP_HOME} ${UI_HOME}

COPY frontend /tmp/frontend
RUN cd /tmp/frontend && npm config set registry https://registry.npmmirror.com && npm install && npm run build && mv dist ${UI_HOME}/dist

# 后端依赖构建阶段
FROM sqlbot-python-pg:local AS sqlbot-builder
ENV PYTHONUNBUFFERED=1
ENV SQLBOT_HOME=/opt/sqlbot
ENV APP_HOME=${SQLBOT_HOME}/app
ENV UI_HOME=${SQLBOT_HOME}/frontend
ENV PYTHONPATH=${SQLBOT_HOME}/app
ENV PATH="${APP_HOME}/.venv/bin:$PATH"
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV DEBIAN_FRONTEND=noninteractive

RUN mkdir -p ${APP_HOME} ${UI_HOME}

WORKDIR ${APP_HOME}

COPY --from=sqlbot-ui-builder ${UI_HOME} ${UI_HOME}
# 依赖锁定安装（uv.lock 必须存在，仓库已生成）
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=backend/uv.lock,target=uv.lock \
    --mount=type=bind,source=backend/pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project

COPY ./backend ${APP_HOME}

# Final sync to ensure all dependencies are installed
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --extra cpu

# g2-ssr 构建阶段
FROM sqlbot-python-pg:local AS ssr-builder
WORKDIR /app
COPY g2-ssr/app.js g2-ssr/package.json /app/
COPY g2-ssr/charts/* /app/charts/
RUN npm config set registry https://registry.npmmirror.com && npm install

# 运行时阶段
FROM sqlbot-python-pg:local
RUN ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    echo "Asia/Shanghai" > /etc/timezone

ENV PYTHONUNBUFFERED=1
ENV SQLBOT_HOME=/opt/sqlbot
ENV PYTHONPATH=${SQLBOT_HOME}/app
ENV PATH="${SQLBOT_HOME}/app/.venv/bin:$PATH"

ENV POSTGRES_DB=sqlbot
ENV POSTGRES_USER=root
ENV POSTGRES_PASSWORD=Password123@pg

# Copy necessary files from builder
COPY installer/start.sh /opt/sqlbot/app/start.sh
# 条件拷贝字体：仓库当前无 .ttf，避免 glob 无匹配导致 BuildKit 构建失败
RUN if ls g2-ssr/*.ttf >/dev/null 2>&1; then mkdir -p /usr/share/fonts/truetype/liberation/ && cp g2-ssr/*.ttf /usr/share/fonts/truetype/liberation/; fi
COPY --from=sqlbot-builder ${SQLBOT_HOME} ${SQLBOT_HOME}
COPY --from=ssr-builder /app /opt/sqlbot/g2-ssr

WORKDIR ${SQLBOT_HOME}/app

RUN mkdir -p /opt/sqlbot/images /opt/sqlbot/g2-ssr

EXPOSE 3000 8000 8001 5432

# Add health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python3 -c "import os, urllib.request; urllib.request.urlopen(f'http://localhost:8000/{os.environ.get(\"CONTEXT_PATH\", \"\")}', timeout=3)" || exit 1

ENTRYPOINT ["sh", "start.sh"]
