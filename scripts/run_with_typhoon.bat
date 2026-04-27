@echo off
REM ===========================================================================
REM THAA launcher using bundled Typhoon HIL Control Center Python.
REM
REM Usage:
REM   scripts\run_with_typhoon.bat pytest tests
REM   scripts\run_with_typhoon.bat python main.py --goal "..."
REM   scripts\run_with_typhoon.bat python main.py --server --port 8000
REM ===========================================================================

REM Auto-detect THCC install. Override by exporting TYPHOON_PY before calling.
if "%TYPHOON_PY%"=="" set TYPHOON_PY=C:\Program Files\Typhoon HIL Control Center 2026.1 sp1\python3_portable\python.exe
if not exist "%TYPHOON_PY%" set TYPHOON_PY=C:\abc\Typhoon HIL Control Center 2026.1 sp1\python3_portable\python.exe

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
REM NOTE: Windows ``shift`` does NOT update ``%*`` -- we must rebuild
REM the argument list manually to avoid passing "python" as the script.
setlocal EnableDelayedExpansion
set "ARGS="
:collect
if "%~2"=="" goto run
set ARGS=!ARGS! %2
shift
goto collect
:run
if /I "%~1"=="python" (
    "%TYPHOON_PY%" !ARGS!
) else if /I "%~1"=="pytest" (
    "%TYPHOON_PY%" -m pytest !ARGS!
) else (
    "%~1" !ARGS!
)
endlocal
