@echo off
chcp 65001 >NUL
cd /d "%~dp0"
set "PYTHON_EXE=%~dp0..\testenv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
echo ============================================================
echo MASTER SNIPER PRE-LIVE TESTS
echo ============================================================
echo.
echo [1/5] Running system and broker-flow simulation...
"%PYTHON_EXE%" SYSTEM_TEST.py
if errorlevel 1 goto failed
echo.
echo [2/5] Running symmetric condition consensus tests...
"%PYTHON_EXE%" SCORING_TEST.py
if errorlevel 1 goto failed
echo.
echo [3/5] Running advanced regime, optimizer, analytics and execution tests...
"%PYTHON_EXE%" ADVANCED_ENGINE_TEST.py
if errorlevel 1 goto failed
echo.
echo [4/5] Running token, expiry and lot-size automation tests...
"%PYTHON_EXE%" TOKEN_AUTOMATION_TEST.py
if errorlevel 1 goto failed
echo.
echo [5/5] Running historical signal replay...
"%PYTHON_EXE%" SIGNAL_BACKTEST.py
if errorlevel 1 goto failed
echo.
echo ============================================================
echo ALL AVAILABLE TESTS PASSED
echo ============================================================
pause
exit /b 0

:failed
echo.
echo ============================================================
echo TEST FAILED - DO NOT START LIVE BOT
echo ============================================================
pause
exit /b 1
