"""docker-compose.yml 的不变量测试（阶段 5.2）。

本机没有 Docker CLI，真实 ``docker compose up`` 交给 CI（见 ci.yml 的
``Validate compose file`` 与 ``docker`` job）。这里只守住编排文件的硬约束：
服务名、镜像、默认端口绑定、具名卷、健康检查、postgres 默认不启动，以及文件
里出现的 CLI 命令必须真实存在（跨文件契约）。
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"
ENV_EXAMPLE = ROOT / ".env.example"
PYPROJECT = ROOT / "pyproject.toml"
DOCKERFILE = ROOT / "Dockerfile"
ENTRYPOINT = ROOT / "docker" / "entrypoint.sh"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _compose_text() -> str:
    return COMPOSE.read_text(encoding="utf-8")


def test_compose_file_exists_and_parses() -> None:
    assert COMPOSE.is_file()
    data = _compose()
    # 新版 compose 不再需要 version: 字段；有反而会触发告警。
    assert "version" not in data
    assert data["name"] == "zhiji"


def test_zhiji_service_basics() -> None:
    svc = _compose()["services"]["zhiji"]
    assert svc["image"] == "zhiji:local"
    assert svc["build"]["dockerfile"] == "Dockerfile"
    assert svc["restart"] == "unless-stopped"


def test_zhiji_defaults_to_loopback_and_auth() -> None:
    svc = _compose()["services"]["zhiji"]
    env = svc["environment"]
    assert env["ZHIJI_HOST"] == "0.0.0.0"
    assert env["ZHIJI_PORT"] == "8000"
    # 默认要求登录：容器绑 0.0.0.0，不能裸奔。
    assert env["ZHIJI_REQUIRE_AUTH"] == "${ZHIJI_REQUIRE_AUTH:-1}"
    # 默认只绑本机回环，要开放须显式 ZHIJI_BIND=0.0.0.0。
    assert svc["ports"] == ["${ZHIJI_BIND:-127.0.0.1}:${ZHIJI_HOST_PORT:-8000}:8000"]


def test_zhiji_state_on_named_volumes() -> None:
    svc = _compose()["services"]["zhiji"]
    assert svc["volumes"] == [
        "zhiji-data:/app/data",
        "zhiji-config:/app/config",
        "zhiji-output:/app/output",
        "zhiji-tmp:/app/.tmp",
    ]
    volumes = _compose()["volumes"]
    for name in ("zhiji-data", "zhiji-config", "zhiji-output", "zhiji-tmp", "zhiji-pgdata"):
        assert name in volumes


def test_zhiji_healthcheck_probes_api_health() -> None:
    hc = _compose()["services"]["zhiji"]["healthcheck"]
    assert hc["interval"] == "30s"
    assert hc["timeout"] == "5s"
    assert hc["start_period"] == "30s"
    assert hc["retries"] == 3
    assert "/api/health" in " ".join(hc["test"])


def test_postgres_is_optional_profile() -> None:
    data = _compose()
    pg = data["services"]["postgres"]
    assert pg["profiles"] == ["postgres"]
    assert pg["image"] == "postgres:16-alpine"
    assert "zhiji-pgdata:/var/lib/postgresql/data" in pg["volumes"]


def test_compose_uses_real_module_command() -> None:
    """文件里的迁移命令必须是 ``python -m zhiji.migrate``，不能是错的
    ``python -m zhiji migrate``（typer 没注册 migrate 子命令）。"""

    text = _compose_text()
    assert "python -m zhiji.migrate" in text
    assert "python -m zhiji migrate" not in text
    # 被引用的模块文件确实存在。
    assert (ROOT / "src" / "zhiji" / "migrate.py").is_file()


def test_postgres_driver_is_optional_dependency() -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    assert 'postgres = ["psycopg[binary]>=3.2"]' in text
    # Dockerfile 要能按构建参数把驱动装进镜像。
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "ARG ZHIJI_WITH_POSTGRES=0" in dockerfile
    assert "postgres" in dockerfile


def test_env_example_documents_compose_vars() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for needle in ("ZHIJI_BIND", "ZHIJI_HOST_PORT", "POSTGRES_USER", "POSTGRES_PASSWORD"):
        assert needle in text, f".env.example 缺少 {needle}"


def test_entrypoint_does_not_mention_broken_migrate() -> None:
    """entrypoint 注释里的迁移命令也要用正确的模块形式。"""

    text = ENTRYPOINT.read_text(encoding="utf-8")
    assert "python -m zhiji migrate" not in text


def test_ci_validates_compose_file() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    script = "\n".join(step.get("run", "") for step in workflow["jobs"]["docker"]["steps"])
    assert "docker compose -f docker-compose.yml config -q" in script