@echo off
REM Fast local dev launcher — uses C: temp venv (G: network venv is slow)
setlocal
set PORT=5002
set ENVIRONMENT=development
set AUTH_ENABLED=false
set ADMIN_AUTH_ENABLED=false
set VENV=C:\Users\HawksEC\AppData\Local\Temp\rowan-gis-chatbot-venv

if not exist "%VENV%\Scripts\python.exe" (
  echo Creating venv at %VENV% ...
  python -m venv "%VENV%"
  if errorlevel 1 (
    echo Failed to create Python venv.
    pause
    exit /b 1
  )
  "%VENV%\Scripts\python.exe" -m pip install -q -r "%~dp0requirements.txt"
  if errorlevel 1 (
    echo Failed to install Python packages.
    pause
    exit /b 1
  )
)

cd /d "%~dp0"

echo.
echo  Stopping any existing GIS Chatbot Python processes ...
for /f "skip=1 tokens=1" %%a in ('wmic process where "CommandLine like '%%rowan-gis-chatbot%%app.py%%'" get ProcessId 2^>nul') do (
  if not "%%a"=="" taskkill /PID %%a /F >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
  taskkill /PID %%a /F >nul 2>&1
)
timeout /t 2 /nobreak >nul

echo.
echo  Starting GIS Chatbot at http://localhost:%PORT%
echo  Press Ctrl+C to stop.
echo.
"%VENV%\Scripts\python.exe" app.py
echo.
echo  Server stopped.
pause
