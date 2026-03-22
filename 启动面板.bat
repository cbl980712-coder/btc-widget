@echo off
chcp 65001 > nul
title 币安合约面板

echo ================================
echo     币安合约判断面板 启动中...
echo ================================

:: 检查 Python
python --version > nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python！
    echo 请去 https://www.python.org/downloads/ 下载安装
    echo 安装时记得勾选 Add Python to PATH
    pause
    exit /b 1
)

:: 安装依赖
echo [1/2] 检查依赖...
python -c "import webview" > nul 2>&1
if errorlevel 1 (
    echo 正在安装 pywebview，请稍等...
    python -m pip install pywebview -q
)

:: 启动桌面窗口
echo [2/2] 启动桌面面板...
cd /d "%~dp0"
python widget.py

pause
