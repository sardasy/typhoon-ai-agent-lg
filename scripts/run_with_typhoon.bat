@echo off
REM ===========================================================================
REM THAA launcher using bundled Typhoon HIL Control Center Python.
REM
REM Usage:
REM   scripts\run_with_typhoon.bat pytest tests
REM   scripts\run_with_typhoon.bat python main.py --goal "..."
REM   scripts\run_with_typhoon.bat python main.py --server --port 8000
REM ===========================================================================

set TYPHOON_PY=C:\abc\Typhoon HIL Control Center 2026.1 sp1\python3_portable\python.exe

if not exist "%TYPHOON_PY%" (
    echo [ERROR] Typhoon HIL Python not found at:
    echo   %TYPHOON_PY%
    echo Adjust TYPHOON_PY in this script for your installation.
    exit /b 1
)

if "%~1"=="" (
    echo Usage: %~nx0 ^<command^> [args...]
    echo Examples:
    echo   %~nx0 python -m pytest tests/ -v
    echo   %~nx0 python main.py --goal "IEEE 2800 GFM compliance"
    exit /b 1
)

REM Replace literal "python" with the Typhoon Python; pass through everything else.
if /I "%~1"=="python" (
    shift
    "%TYPHOON_PY%" %*
) else if /I "%~1"=="pytest" (
    shift
    "%TYPHOON_PY%" -m pytest %*
) else (
    "%~1" %*
)
