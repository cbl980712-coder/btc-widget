@echo off
title BTC Widget
cd /d "%~dp0"
echo [1/2] Checking dependencies...
python -c "import webview" > nul 2>&1
if errorlevel 1 (
    echo Installing pywebview...
    python -m pip install pywebview -q -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
)
echo [2/2] Starting widget...
python widget.py
pause
