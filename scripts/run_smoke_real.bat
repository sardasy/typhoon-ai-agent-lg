@echo off
REM Phase 4-H real-hardware smoke runner.
REM
REM Runs the THAA pre-flight + a single passing scenario against a
REM connected HIL404 (and, optionally, a real ECU via XCP). Use this
REM AFTER scripts\run_with_typhoon.bat works for plain pytest.
REM
REM Usage:
REM   scripts\run_smoke_real.bat                 (HIL only)
REM   scripts\run_smoke_real.bat firmware.a2l    (HIL + ECU XCP)
REM
REM Exit codes:
REM   0  smoke passed
REM   1  preflight FAILED -- inspect output, fix env, re-run
REM   2  preflight WARNed in --strict mode (we don't use --strict here
REM      so this only fires if you set THAA_PREFLIGHT_STRICT=1)
REM   N  THAA exit code (graph error / scenario error / etc.)

setlocal
set REPO=%~dp0..
set A2L=%~1

echo == Step 1/3: pre-flight ==
if defined A2L (
    call "%REPO%\scripts\run_with_typhoon.bat" python "%REPO%\scripts\preflight.py" --a2l-path "%A2L%"
) else (
    call "%REPO%\scripts\run_with_typhoon.bat" python "%REPO%\scripts\preflight.py"
)
if errorlevel 1 (
    echo Pre-flight failed. Aborting smoke.
    exit /b 1
)

echo.
echo == Step 2/3: minimal HIL run (BMS overvoltage, 1 scenario) ==
if defined A2L (
    call "%REPO%\scripts\run_with_typhoon.bat" python "%REPO%\main.py" ^
        --goal "BMS overvoltage smoke" ^
        --config "%REPO%\configs\scenarios.yaml" ^
        --dut-backend hybrid --a2l-path "%A2L%"
) else (
    call "%REPO%\scripts\run_with_typhoon.bat" python "%REPO%\main.py" ^
        --goal "BMS overvoltage smoke" ^
        --config "%REPO%\configs\scenarios.yaml"
)
if errorlevel 1 (
    echo HIL smoke run failed.
    exit /b %ERRORLEVEL%
)

echo.
echo == Step 3/3: orchestrator + twin (no apply_fix expected) ==
call "%REPO%\scripts\run_with_typhoon.bat" python "%REPO%\main.py" ^
    --goal "Multi-agent smoke" ^
    --config "%REPO%\configs\scenarios.yaml" ^
    --orchestrator --twin
if errorlevel 1 (
    echo Orchestrator smoke run failed.
    exit /b %ERRORLEVEL%
)

echo.
echo == All smoke steps passed ==
exit /b 0
