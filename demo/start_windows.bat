@echo off
setlocal
cd /d "%~dp0"
echo [M01 DEMO] running tests first...
call npm test
if errorlevel 1 (
  echo [M01 DEMO] tests failed, aborting start.
  exit /b 1
)
echo [M01 DEMO] starting on http://localhost:3101
call npm start
endlocal
