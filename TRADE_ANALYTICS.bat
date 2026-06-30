@echo off
chcp 65001 >NUL
cd /d "%~dp0"
set "PYTHON_EXE=%~dp0..\testenv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
"%PYTHON_EXE%" trade_analytics.py
pause
