"""知记 CLI。"""

from __future__ import annotations

import shutil
import sys

import typer
from rich.console import Console
from rich.table import Table

from zhiji.config import ConfigManager, mask_api_key
from zhiji.errors import ZhijiError
from zhiji.models import LLMModelConfig, Platform
from zhiji.pipeline import NotePipeline

app = typer.Typer(name="zhiji", help="知记：多平台知识内容 -> Markdown 笔记", no_args_is_help=True)
console = Console()


def _console_safe_text(value: object, *, encoding: str | None = None) -> str:
    """Return text that the active Windows console can encode.

    PowerShell 5.1 commonly uses GBK. A valid filename may contain emoji, and
    Rich's legacy Windows renderer otherwise raises UnicodeEncodeError while
    printing a successful receipt. Only the display text is softened; the real
    filename remains unchanged.
    """

    text = str(value)
    target_encoding = encoding or getattr(console.file, "encoding", None) or "utf-8"
    try:
        return text.encode(target_encoding, errors="replace").decode(target_encoding)
    except LookupError:
        return text


def _manager() -> ConfigManager:
    return ConfigManager()


def _handle_error(exc: Exception) -> None:
    if isinstance(exc, ZhijiError):
        hint = f"\n提示: {exc.hint}" if exc.hint else ""
        console.print(f"[red]{exc.message}[/red]{hint}")
    else:
        console.print(f"[red]未知错误: {exc}[/red]")
    raise typer.Exit(1)


@app.command()
def note(
    url: str = typer.Argument(..., help="B 站 / 知乎 / 抖音内容链接"),
    model: str | None = typer.Option(None, help="指定模型名称"),
    output_dir: str | None = typer.Option(None, help="笔记输出目录"),
    stream: bool = typer.Option(False, "--stream", "-s", help="启用流式输出"),
) -> None:
    """从链接生成 Markdown 笔记。"""
    try:
        def stream_callback(chunk: str) -> None:
            """流式输出回调函数"""
            console.print(chunk, end="", highlight=False)

        def progress_callback(stage: str, message: str, status: str = "start") -> None:
            if status == "ok":
                console.print(f"[green]✓[/green] {_console_safe_text(message)}")
            else:
                console.print(f"[dim]▸ {_console_safe_text(message)}[/dim]")

        receipt = NotePipeline(_manager()).run(
            url,
            model_name=model,
            output_dir=output_dir,
            stream_callback=stream_callback if stream else None,
            progress_callback=progress_callback,
        )
        if stream:
            console.print()  # 换行
    except Exception as exc:  # noqa: BLE001
        _handle_error(exc)
    console.print(f"[green]OK[/green] {_console_safe_text(receipt.note_path)}")
    if receipt.transcript_path:
        console.print(f"[green]转写文本[/green] {_console_safe_text(receipt.transcript_path)}")
    for warning in receipt.warnings:
        console.print(f"[yellow]提示[/yellow] {warning}")


@app.command()
def search(
    keyword: str = typer.Argument(..., help="搜索关键词"),
    platform: str = typer.Option("bilibili", help="bilibili / zhihu / douyin"),
    limit: int = typer.Option(10, min=1, max=50),
    pick: int | None = typer.Option(None, help="直接选择第 N 条，跳过交互"),
) -> None:
    """搜索平台内容并生成笔记。"""
    try:
        platform_enum = Platform(platform)
    except ValueError:
        console.print(f"[red]不支持的平台: {platform}[/red]")
        raise typer.Exit(1)
    try:
        results = NotePipeline(_manager()).search(keyword, platform_enum, limit=limit)
    except Exception as exc:  # noqa: BLE001
        _handle_error(exc)
    if not results:
        console.print("[yellow]没有找到结果[/yellow]")
        return
    table = Table(title=f"{platform_enum.value} 搜索结果")
    for column in ["#", "标题", "作者", "时长"]:
        table.add_column(column)
    for index, item in enumerate(results, start=1):
        table.add_row(str(index), item.title, item.author or "-", str(item.duration or "-"))
    console.print(table)
    choice = pick if pick is not None else typer.prompt("输入编号（Enter 选 1）", default=1, type=int)
    if not 1 <= choice <= len(results):
        console.print("[red]编号无效[/red]")
        raise typer.Exit(1)
    try:
        def stream_callback(chunk: str) -> None:
            console.print(chunk, end="", highlight=False)
        
        receipt = NotePipeline(_manager()).run(
            results[choice - 1].url,
            stream_callback=stream_callback,
        )
        console.print()  # 换行
    except Exception as exc:  # noqa: BLE001
        _handle_error(exc)
    console.print(f"[green]OK[/green] {_console_safe_text(receipt.note_path)}")


@app.command("models")
def models_list() -> None:
    """列出模型配置。"""
    config = _manager().get()
    table = Table(title="模型配置")
    for column in ["名称", "Base URL", "模型", "默认", "状态", "API Key"]:
        table.add_column(column)
    for model in config.models:
        table.add_row(
            model.name,
            model.base_url,
            model.model,
            "是" if model.default else "",
            "启用" if model.enabled else "停用",
            mask_api_key(model.api_key),
        )
    console.print(table)


@app.command("model-add")
def model_add(
    name: str,
    base_url: str = typer.Option("https://api.deepseek.com/v1", help="OpenAI 兼容 Base URL"),
    model: str = typer.Option("deepseek-chat"),
    api_key: str = typer.Option("", help="API Key"),
    enabled: bool = typer.Option(True),
    default: bool = typer.Option(False),
    note_text: str = typer.Option("", "--note"),
) -> None:
    try:
        saved = _manager().add_model(
            LLMModelConfig(
                name=name,
                base_url=base_url,
                model=model,
                api_key=api_key,
                enabled=enabled,
                default=default,
                note=note_text,
            )
        )
    except Exception as exc:  # noqa: BLE001
        _handle_error(exc)
    console.print(f"[green]已添加[/green] {saved.name}")


@app.command("model-remove")
def model_remove(name: str) -> None:
    try:
        _manager().delete_model(name)
    except Exception as exc:  # noqa: BLE001
        _handle_error(exc)
    console.print(f"[green]已删除[/green] {name}")


@app.command("model-default")
def model_default(name: str) -> None:
    try:
        _manager().set_default_model(name)
    except Exception as exc:  # noqa: BLE001
        _handle_error(exc)
    console.print(f"[green]默认模型已切换[/green] {name}")


@app.command("config-show")
def config_show() -> None:
    """查看当前设置。"""
    for key, value in _manager().get().settings.model_dump(mode="json").items():
        console.print(f"{key}: {value}")


@app.command("zhihu-login")
def zhihu_login() -> None:
    """打开有头 Edge 完成登录，并保存知乎登录态（Cookie + UA）。"""

    from zhiji.platforms.zhihu import login_with_browser

    try:
        profile = login_with_browser()
    except ZhijiError as exc:
        _handle_error(exc)
    console.print(
        f"[green]知乎登录态已保存[/green]（{len(profile.cookie)} 字节 Cookie，"
        f"保存于 {profile.saved_at}）"
    )


@app.command("douyin-login")
def douyin_login() -> None:
    """打开浏览器完成登录，并保存抖音登录态（Cookie + UA）。"""

    from zhiji.platforms.douyin import login_with_browser

    try:
        login_with_browser()
    except ZhijiError as exc:
        _handle_error(exc)
    console.print("[green]抖音登录态已保存[/green]")


@app.command("bilibili-login")
def bilibili_login() -> None:
    """打开浏览器完成登录，并保存 B 站登录态（Cookie + UA）。

    登录后 B 站视频优先使用官方字幕（含 AI 字幕），跳过本地音频转写。
    """

    from zhiji.platforms.bilibili import login_with_browser

    try:
        profile = login_with_browser()
    except ZhijiError as exc:
        _handle_error(exc)
    console.print(
        f"[green]B 站登录态已保存[/green]（{len(profile.cookie)} 字节 Cookie，"
        f"保存于 {profile.saved_at}）"
    )


@app.command("check-env")
def check_env() -> None:
    """检查环境依赖。"""
    checks = {
        "Python": sys.version.split()[0],
        "FFmpeg": shutil.which("ffmpeg") or "未找到",
        "yt-dlp": _importable("yt_dlp"),
        "faster-whisper": _importable("faster_whisper"),
        "配置文件": str(_manager().path),
    }
    for key, value in checks.items():
        console.print(f"{key}: {value}")


@app.command("web")
def web(
    port: int = 8000,
    host: str = "127.0.0.1",
    token: str | None = typer.Option(None, help="非本机绑定时必须提供的访问令牌"),
) -> None:
    """启动本地 Web 配置页。

    绑定到非本机地址（如 0.0.0.0）会暴露 API Key / Cookie 管理接口，
    因此必须同时提供 --token，访问时需带上 ?token=。
    """
    import uvicorn

    from zhiji.web.app import create_app

    is_local = host in {"127.0.0.1", "localhost", "::1"}
    if not is_local and not token:
        console.print(
            "[red]拒绝启动：绑定非本机地址必须提供 --token[/red]\n"
            "[yellow]示例：zhiji web --host 0.0.0.0 --token <你的令牌>[/yellow]"
        )
        raise typer.Exit(1)
    access = f"http://{host}:{port}"
    if token:
        access += f"/?token={token}"
    console.print(f"打开 {access}")
    uvicorn.run(create_app(_manager(), token=token), host=host, port=port)


def _importable(module: str) -> str:
    try:
        __import__(module)
        return "可用"
    except ImportError:
        return "未安装"


def app_entry() -> None:
    app()
