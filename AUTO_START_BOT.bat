@echo off
chcp 65001 >NUL
title MASTER SNIPER LIVE BOT
cd /d "%~dp0"
set "PYTHON_EXE=%~dp0..\testenv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
echo Starting MASTER SNIPER live bot...
"%PYTHON_EXE%" bot.py
echo.
echo Bot stopped. Check logs\bot.log for details.
pause
