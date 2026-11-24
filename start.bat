@echo off
chcp 65001 >nul
cd /d "%~dp0"
title ClipLingua
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" app\server.py --open %*
) else (
  python app\server.py --open %*
)
if errorlevel 1 pause
