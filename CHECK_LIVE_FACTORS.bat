@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=%~dp0..\testenv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
echo ============================================================
echo MASTER SNIPER VERIFIED LIVE FACTOR CHECK
echo ============================================================
"%PYTHON_EXE%" CHECK_LIVE_FACTORS.py
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" (
    echo One or more live sources are currently unavailable.
) else (
    echo All required live factors are available.
)
pause
exit /b %EXIT_CODE%
