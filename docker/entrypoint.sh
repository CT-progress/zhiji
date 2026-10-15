#!/bin/sh
# 知记（zhiji）容器入口。
#
# 默认行为：前台启动 Web 服务
#   docker run -p 8000:8000 -v zhiji-data:/app/data zhiji
#
# 传入命令时直接执行该命令，便于跑一次性任务（迁移、体检等）：
#   docker run --rm -v zhiji-data:/app/data zhiji python -m zhiji.migrate --migrate-keys
#
# 可用环境变量：
#   ZHIJI_HOST          监听地址，默认 0.0.0.0
#   ZHIJI_PORT          监听端口，默认 8000
#   ZHIJI_TOKEN         旧版共享访问令牌（可选）
#   ZHIJI_REQUIRE_AUTH  1 = 要求账号登录（默认）；0 = 关闭，此时必须自带令牌
set -eu

# 显式传了命令就照原样执行，不再拼默认参数。
if [ "$#" -gt 0 ]; then
    exec "$@"
fi

HOST="${ZHIJI_HOST:-0.0.0.0}"
PORT="${ZHIJI_PORT:-8000}"
AUTH_LABEL="关闭"

set -- web --host "$HOST" --port "$PORT"

if [ -n "${ZHIJI_TOKEN:-}" ]; then
    set -- "$@" --token "$ZHIJI_TOKEN"
fi

# 容器默认绑 0.0.0.0，因此默认要求登录；关闭时由命令行自身的安全检查兜底。
if [ "${ZHIJI_REQUIRE_AUTH:-1}" != "0" ]; then
    set -- "$@" --require-auth
    AUTH_LABEL="开启（首次使用请先在页面注册账号）"
fi

echo "[zhiji] Web 服务：http://${HOST}:${PORT}  账号鉴权：${AUTH_LABEL}"
exec python -m zhiji "$@"