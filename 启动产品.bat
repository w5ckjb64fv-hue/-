@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=python
%PY% --version >nul 2>&1
if errorlevel 1 (
  set PY=py
  %PY% --version >nul 2>&1
  if errorlevel 1 (
    echo [错误] 没有检测到 Python。请先安装 Python 3.10 或更高版本，并勾选 Add Python to PATH。
    pause
    exit /b 1
  )
)
if not exist .env copy .env.example .env >nul
start "" http://127.0.0.1:7860
%PY% app.py
pause
