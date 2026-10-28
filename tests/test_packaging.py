"""容器化打包文件的不变量测试（阶段 5.1）。

这些用例不打 Docker（CI 的 docker job 才做真实构建），只守住几条容易被无意破
坏的硬约束：

* 入口脚本是 POSIX sh、以 LF 结尾、默认要求登录、能透传显式命令；
* 入口脚本硬编码的 CLI 选项必须真实存在（跨文件契约）；
* Dockerfile 内置 Vue 构建阶段、以非 root 运行、内置 Playwright、带健康检查、可写状态走卷；
* ``.dockerignore`` 必须挡住本机状态与密钥，避免被 `docker build` 烤进镜像。
"""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.main import get_command

from zhiji.cli import app as cli_app

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
ENTRYPOINT = ROOT / "docker" / "entrypoint.sh"
QUICKSTART = ROOT / "scripts" / "quickstart.bat"
SMOKE_TEST = ROOT / "scripts" / "smoke_test.py"


def _dockerfile() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def _entrypoint() -> str:
    return ENTRYPOINT.read_text(encoding="utf-8")


def test_docker_assets_exist() -> None:
    assert DOCKERFILE.is_file()
    assert DOCKERIGNORE.is_file()
    assert ENTRYPOINT.is_file()


def test_entrypoint_is_posix_shell_with_lf_endings() -> None:
    raw = ENTRYPOINT.read_bytes()
    # Windows 检出会带 CRLF，容器里执行会变成 “bad interpreter”，所以这里必须守住 LF。
    assert b"\r" not in raw
    text = raw.decode("utf-8")
    assert text.startswith("#!/bin/sh\n")
    assert "set -eu" in text


def test_entrypoint_forwards_explicit_command() -> None:
    text = _entrypoint()
    # 显式传命令（如 python -m zhiji migrate）时必须原样执行，不拼默认参数。
    assert 'if [ "$#" -gt 0 ]; then' in text
    assert 'exec "$@"' in text


def test_entrypoint_defaults_to_require_auth_and_honours_env() -> None:
    text = _entrypoint()
    assert '"${ZHIJI_REQUIRE_AUTH:-1}"' in text
    assert 'set -- "$@" --require-auth' in text
    assert 'HOST="${ZHIJI_HOST:-0.0.0.0}"' in text
    assert 'PORT="${ZHIJI_PORT:-8000}"' in text
    assert 'ZHIJI_TOKEN' in text
    assert text.rstrip().endswith('exec python -m zhiji "$@"')


def test_entrypoint_flags_exist_in_cli() -> None:
    """入口脚本拼的参数名必须和 CLI 真实选项一致。

    直接查命令定义而不是渲染后的 --help 文本：rich 在窄终端会把帮助信息折行
    截断，导致文本断言在 CI 上假失败（见 v0.1.0 后 CI checks job）。
    """

    web_command = get_command(cli_app).commands["web"]
    opts = {opt for param in web_command.params for opt in getattr(param, "opts", ())}
    for flag in ("--host", "--port", "--token", "--require-auth"):
        assert flag in opts


def test_dockerfile_runs_as_non_root() -> None:
    text = _dockerfile()
    assert "USER zhiji" in text
    assert "uid 10001" in text
    # 结尾不能再切回 root，否则容器里跑的就是 root 进程。
    assert text.rstrip().splitlines()[-1].strip() == 'ENTRYPOINT ["/usr/local/bin/zhiji-entrypoint"]'


def test_dockerfile_builds_and_ships_vue_frontend() -> None:
    text = _dockerfile()
    assert "FROM node:20-bookworm-slim AS frontend-builder" in text
    assert "COPY frontend/package.json frontend/package-lock.json ./" in text
    assert "npm ci --no-audit --no-fund" in text
    assert "npm run build" in text
    assert (
        "COPY --from=frontend-builder --chown=zhiji:zhiji /app/frontend/dist ./frontend/dist"
        in text
    )
    # FastAPI 从项目根目录读取 frontend/dist，容器内路径必须与之一致。
    app_source = (ROOT / "src" / "zhiji" / "web" / "app.py").read_text("utf-8")
    assert 'parents[3] / "frontend" / "dist"' in app_source


def test_dockerfile_installs_playwright_and_healthcheck() -> None:
    text = _dockerfile()
    assert "playwright install --with-deps chromium" in text
    assert "HEALTHCHECK" in text
    assert "/api/health" in text
    # 健康检查打的是真实存在的路由。
    assert '"/api/health"' in (ROOT / "src" / "zhiji" / "web" / "app.py").read_text("utf-8")


def test_dockerfile_keeps_state_on_volumes() -> None:
    text = _dockerfile()
    for mount in ("/app/data", "/app/config", "/app/output", "/app/.tmp"):
        assert mount in text
    assert "VOLUME" in text
    assert "EXPOSE 8000" in text


def test_dockerfile_does_not_bake_secrets() -> None:
    text = _dockerfile()
    # 不能把整个工作目录 COPY 进来，也不能复制本机状态与密钥。
    assert "COPY . ." not in text
    for forbidden in ("COPY .env", "COPY data", "COPY config", "COPY .tmp", "ZHIJI_MASTER_KEY"):
        assert forbidden not in text
    # 运行期状态目录只能是镜像里新建的空目录 + 卷挂载。
    assert "mkdir -p /app/data /app/config /app/output /app/.tmp" in text


def test_dockerignore_blocks_local_state_and_secrets() -> None:
    patterns = {
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    for expected in (
        "data/",
        "config/",
        "output/",
        ".env",
        ".env.*",
        ".master_key",
        ".venv",
        "frontend/node_modules/",
        "frontend/dist/",
    ):
        assert expected in patterns, f".dockerignore 缺少 {expected}"
    assert patterns.isdisjoint(
        {"src/", "pyproject.toml", "README.md", "frontend/src/", "frontend/package.json"}
    )


def test_windows_quickstart_checks_node_and_builds_frontend() -> None:
    text = QUICKSTART.read_text(encoding="utf-8")
    assert "Node.js 18+" in text
    assert "npm.cmd ci --no-audit --no-fund" in text
    assert "npm.cmd run build" in text
    assert "pushd frontend" in text
    assert "python -m zhiji migrate" in text
    assert "python -m zhiji web --port 8000" in text


def test_smoke_test_forces_utf8_output() -> None:
    text = SMOKE_TEST.read_text(encoding="utf-8")
    assert 'sys.stdout.reconfigure(encoding="utf-8", errors="replace")' in text
    assert 'sys.stderr.reconfigure(encoding="utf-8", errors="replace")' in text


def test_ci_builds_frontend_and_probes_the_container() -> None:
    """本机没有 Docker CLI，CI 的 docker job 是唯一的真实构建验证点。"""

    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    frontend = workflow["jobs"]["frontend"]
    frontend_script = "\n".join(step.get("run", "") for step in frontend["steps"])
    assert "npm ci --no-audit --no-fund" in frontend_script
    assert "npm run build" in frontend_script
    job = workflow["jobs"]["docker"]
    assert job["runs-on"] == "ubuntu-latest"
    script = "\n".join(step.get("run", "") for step in job["steps"])
    assert "docker build -t zhiji:ci ." in script
    assert "/api/health" in script
    assert 'test "$uid" = "10001"' in script