# 知记（zhiji）镜像
#
# 设计要点：
#   1. 多阶段构建：Node 阶段构建 Vue 工作台，Python 阶段准备虚拟环境，runtime 只拷贝产物与源码；
#   2. 以「可编辑安装」的方式把包装在 /app/src（editable install），这样
#      zhiji.config.PROJECT_ROOT 仍然指向 /app，data/ config/ output/ .tmp/
#      全部落在卷挂载点上，而不是散进 site-packages；
#   3. Playwright 浏览器及其系统依赖内置在镜像里，运行期不需要联网安装；
#   4. 以非 root 用户（uid/gid 10001）运行，容器内不落 root 进程；
#   5. 所有可写状态（数据库、主密钥、抓取产物）都通过卷挂载，不烤进镜像。

ARG PYTHON_VERSION=3.12

# ---------- 前端构建阶段：产出 frontend/dist ----------
FROM node:20-bookworm-slim AS frontend-builder

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build

# ---------- Python 构建阶段：只负责产出 /opt/venv ----------
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

# 默认依赖都有 manylinux wheel；装编译工具只是兜底，构建完即被丢弃。
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

# 可选：构建带语音转写能力的镜像（体积明显变大，需要 faster-whisper）。
ARG ZHIJI_WITH_ASR=0
# 可选：把 PostgreSQL 驱动 psycopg 装进镜像，供 docker-compose 的 postgres 档使用。
ARG ZHIJI_WITH_POSTGRES=0
RUN python -m venv /opt/venv \
 && /opt/venv/bin/python -m pip install --upgrade pip setuptools wheel \
 && extras='' \
 && if [ "${ZHIJI_WITH_ASR}" = "1" ]; then extras="${extras:+$extras,}asr"; fi \
 && if [ "${ZHIJI_WITH_POSTGRES}" = "1" ]; then extras="${extras:+$extras,}postgres"; fi \
 && if [ -n "${extras}" ]; then target=".[${extras}]"; else target='.'; fi \
 && /opt/venv/bin/python -m pip install -e "${target}"

# ---------- 运行阶段 ----------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    HOME=/home/zhiji \
    HF_HOME=/app/.cache/huggingface \
    ZHIJI_HOST=0.0.0.0 \
    ZHIJI_PORT=8000 \
    ZHIJI_REQUIRE_AUTH=1

# 运行期依赖：ffmpeg（yt-dlp 抽音轨）+ Playwright 浏览器的系统库。
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

# 浏览器落在 /ms-playwright；非 root 启动 Chromium 需要沙箱助手带 setuid 位。
RUN /opt/venv/bin/python -m playwright install --with-deps chromium \
 && chmod -R a+rX /ms-playwright \
 && for sandbox in /ms-playwright/chromium-*/chrome-linux/chrome_sandbox; do \
      [ -e "$sandbox" ] && chmod 4755 "$sandbox" || true; \
    done \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 zhiji \
 && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin zhiji

WORKDIR /app

COPY --chown=zhiji:zhiji pyproject.toml README.md ./
COPY --chown=zhiji:zhiji src ./src
COPY --from=frontend-builder --chown=zhiji:zhiji /app/frontend/dist ./frontend/dist
COPY --chown=zhiji:zhiji docker/entrypoint.sh /usr/local/bin/zhiji-entrypoint

# 源码树在 Windows 上检出可能是 CRLF，运行时统一成 LF 再赋可执行权限。
RUN sed -i 's/\r$//' /usr/local/bin/zhiji-entrypoint \
 && chmod 0755 /usr/local/bin/zhiji-entrypoint \
 && mkdir -p /app/data /app/config /app/output /app/.tmp /app/.cache \
 && chown -R zhiji:zhiji /app

# 可写状态一律走卷：数据库、主密钥、模型配置导出、抓取产物、临时音频。
VOLUME ["/app/data", "/app/config", "/app/output", "/app/.tmp"]

USER zhiji

EXPOSE 8000

# 健康检查用容器内 Python 直接打接口（exec 形式，不经 shell，端口跟随 ZHIJI_PORT）。
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["/opt/venv/bin/python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('ZHIJI_PORT', '8000') + '/api/health', timeout=4)"]

ENTRYPOINT ["/usr/local/bin/zhiji-entrypoint"]