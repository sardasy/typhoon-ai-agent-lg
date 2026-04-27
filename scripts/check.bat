@echo off
REM One-shot quality gate. Runs:
REM   1. pytest with coverage (fails below 60%%)
REM   2. mypy on src/ + scripts/ (canary modules strict)
REM
REM Use this before committing or in CI. For inner-loop dev, just
REM ``pytest tests/`` (no coverage) and ``mypy src/twin.py`` etc.

setlocal
set REPO=%~dp0..
set PY=python

echo == 1/2: pytest --cov + Allure ==
%PY% -m pytest --cov --cov-report=term-missing ^
    --alluredir=reports/allure_results -q
if errorlevel 1 (
    echo ^>^>^> Tests or coverage gate failed. ^<^<^<
    exit /b 1
)

REM Allure HTML is optional -- only generate when the CLI is on PATH.
where allure >nul 2>nul
if not errorlevel 1 (
    echo Allure CLI detected: regenerating reports/allure_html
    allure generate reports/allure_results -o reports/allure_html --clean
)

echo.
echo == 2/2: mypy ==
%PY% -m mypy
if errorlevel 1 (
    echo ^>^>^> mypy reported issues. ^<^<^<
    exit /b 1
)

echo.
echo == All quality checks passed ==
exit /b 0
