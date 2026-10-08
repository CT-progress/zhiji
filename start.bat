@echo off
rem Switch to the directory this script lives in (project root).
cd /d "%~dp0"
rem Point Playwright browsers inside the project unless already set.
if not defined PLAYWRIGHT_BROWSERS_PATH set "PLAYWRIGHT_BROWSERS_PATH=%~dp0ms-playwright"
.\.venv\Scripts\python.exe -m zhiji web --port 8000
