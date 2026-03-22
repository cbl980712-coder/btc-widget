@echo off
chcp 65001 > nul
title BTC 合约判断面板

echo ================================
echo   BTC 合约判断面板 - 启动中
echo ================================
echo.

:: 检查 Python
python --version > nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.x
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: 安装 requests（如果没有）
echo [1/2] 检查依赖...
python -c "import requests" > nul 2>&1
if errorlevel 1 (
    echo 正在安装 requests...
    python -m pip install requests -q
)

:: 启动服务器（后台）
echo [2/2] 启动后台服务器...
start /B python "%~dp0server.py"

:: 等待服务器启动
timeout /t 2 /nobreak > nul

:: 打开浏览器
echo 正在打开面板...
start "" "%~dp0dashboard.html"

echo.
echo ================================
echo   面板已启动！
echo   服务器运行在 127.0.0.1:8765
echo   关闭本窗口会停止服务器
echo ================================
echo.
echo 按任意键停止服务器并退出...
pause > nul

:: 停止 Python 服务器
taskkill /F /IM python.exe /FI "WINDOWTITLE eq BTC*" > nul 2>&1
echo 服务器已停止。
