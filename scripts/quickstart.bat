@echo off
setlocal enabledelayedexpansion
rem ============================================================
rem  知记 Zhiji — 一键启动脚本（Windows）
rem  用法：双击或命令行执行 scripts\quickstart.bat
rem  首次运行自动创建 venv、安装依赖、迁移数据库
rem ============================================================
cd /d "%~dp0\.."

echo [1/5] 检查 Python ...
where python >nul 2>&1
if errorlevel 1 (
    echo 错误：未找到 Python，请先安装 Python 3.11+
    exit /b 1
)

echo [2/5] 创建虚拟环境 ...
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
)
call .\.venv\Scripts\Activate.bat

echo [3/5] 安装依赖 ...
pip install -e ".[dev,asr]" -q

echo [4/5] 安装 Playwright 浏览器 ...
if not defined PLAYWRIGHT_BROWSERS_PATH set "PLAYWRIGHT_BROWSERS_PATH=%CD%\ms-playwright"
playwright install chromium --with-deps 2>nul

echo [5/5] 迁移数据库 + 启动服务 ...
python -m zhiji migrate
echo.
echo ====================================================
echo   知记已启动：http://127.0.0.1:8000
echo   按 Ctrl+C 停止
echo ====================================================
python -m zhiji web --port 8000
