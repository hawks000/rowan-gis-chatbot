@echo off
REM Stop ALL GIS Chatbot instances on port 5002
setlocal
set PORT=5002
set FOUND=0

echo Stopping anything listening on port %PORT% ...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
  set FOUND=1
  echo   Killing PID %%a
  taskkill /PID %%a /F >nul 2>&1
)

timeout /t 2 /nobreak >nul

netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
  echo.
  echo  WARNING: Port %PORT% may still be in use. Re-run this script or reboot.
) else (
  echo.
  echo  Port %PORT% is free. You can run start-local.bat now.
)
echo.
pause
