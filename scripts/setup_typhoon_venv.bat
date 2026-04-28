@echo off
REM ===========================================================================
REM Create (or rebuild) .venv_typhoon so pytest and main.py run against the
REM bundled Typhoon HIL Python 3.11 with THAA's own langgraph / langchain
REM dependencies installed on top.
REM
REM Design:
REM   - Base interpreter is Typhoon's bundled python3_portable so typhoon.api.hil
REM     and typhoon.test.capture are importable.
REM   - Uses --system-site-packages so Typhoon's numpy/matplotlib/requests stay
REM     intact; we only add packages Typhoon does not already ship.
REM   - Leaves the existing venv in place if it already works; no forced rebuild.
REM
REM Usage:
REM   scripts\setup_typhoon_venv.bat
REM   .venv_typhoon\Scripts\python.exe -m pytest tests/ -p no:typhoon -p no:allure_livecli
REM ===========================================================================

set TYPHOON_PY=C:\abc\Typhoon HIL Control Center 2026.1 sp1\python3_portable\python.exe

if not exist "%TYPHOON_PY%" (
    echo [ERROR] Typhoon HIL Python not found at:
    echo   %TYPHOON_PY%
    echo Adjust TYPHOON_PY in this script for your installation.
    exit /b 1
)

set VENV=.venv_typhoon

if not exist "%VENV%\Scripts\python.exe" (
    echo [1/2] Creating %VENV% from %TYPHOON_PY%
    "%TYPHOON_PY%" -m venv --system-site-packages "%VENV%"
    if errorlevel 1 exit /b 1
) else (
    echo [1/2] %VENV% already exists, reusing
)

echo [2/2] Installing THAA deps on top of Typhoon's site-packages
"%VENV%\Scripts\python.exe" -m pip install --quiet ^
    "langgraph>=0.2.0" ^
    "langchain-anthropic>=0.3.0" ^
    "langchain-core>=0.3.0" ^
    "langsmith>=0.1.0" ^
    "pydantic>=2.0" ^
    "pyyaml>=6.0" ^
    "jinja2>=3.1" ^
    "beautifulsoup4>=4.12" ^
    "pytest-asyncio>=0.24"
if errorlevel 1 exit /b 1

echo.
echo DONE.
echo.
echo Run the test suite via:
echo   %VENV%\Scripts\python.exe -m pytest tests/ -p no:typhoon -p no:allure_livecli
echo.
echo Run the agent:
echo   %VENV%\Scripts\python.exe main.py --goal "..." --config configs\dab_smoke.yaml
