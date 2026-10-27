@echo off
setlocal enabledelayedexpansion
rem ============================================================
rem  知记 Zhiji — 一键启动脚本（Windows）
rem  用法：双击或命令行执行 scripts\quickstart.bat
rem  首次运行自动创建 venv、安装依赖、构建前端、迁移数据库
rem ============================================================
cd /d "%~dp0\.."

echo [1/7] 检查 Python ...
where python >nul 2>&1
if errorlevel 1 (
    echo 错误：未找到 Python，请先安装 Python 3.11+
    exit /b 1
)

echo [2/7] 检查 Node.js ...
where node >nul 2>&1
if errorlevel 1 (
    echo 错误：未找到 Node.js，请先安装 Node.js 18+
    exit /b 1
)
where npm >nul 2>&1
if errorlevel 1 (
    echo 错误：未找到 npm，请重新安装 Node.js 18+
    exit /b 1
)
node -e "process.exit(Number(process.versions.node.split('.')[0]) >= 18 ? 0 : 1)"
if errorlevel 1 (
    echo 错误：Node.js 版本过低，请安装 18 或更高版本
    exit /b 1
)

echo [3/7] 创建虚拟环境 ...
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
)
call .\.venv\Scripts\Activate.bat

echo [4/7] 安装后端依赖 ...
pip install -e ".[dev,asr]" -q

echo [5/7] 构建 Vue 工作台 ...
pushd frontend
call npm.cmd ci --no-audit --no-fund
if errorlevel 1 (
    popd
    echo 错误：前端依赖安装失败
    exit /b 1
)
call npm.cmd run build
if errorlevel 1 (
    popd
    echo 错误：前端构建失败
    exit /b 1
)
popd

echo [6/7] 安装 Playwright 浏览器 ...
if not defined PLAYWRIGHT_BROWSERS_PATH set "PLAYWRIGHT_BROWSERS_PATH=%CD%\ms-playwright"
playwright install chromium --with-deps 2>nul

echo [7/7] 迁移数据库 + 启动服务 ...
python -m zhiji migrate
echo.
echo ====================================================
echo   知记已启动：http://127.0.0.1:8000
echo   按 Ctrl+C 停止
echo ====================================================
python -m zhiji web --port 8000
