@echo off
REM Fast local dev launcher — uses C: temp venv (G: network venv is slow)
set PORT=5002
set ENVIRONMENT=development
set AUTH_ENABLED=false
set ADMIN_AUTH_ENABLED=false
set VENV=C:\Users\HawksEC\AppData\Local\Temp\rowan-gis-chatbot-venv

if not exist "%VENV%\Scripts\python.exe" (
  echo Creating venv at %VENV% ...
  python -m venv "%VENV%"
  "%VENV%\Scripts\python.exe" -m pip install -q -r "%~dp0requirements.txt"
)

cd /d "%~dp0"
echo Starting GIS Chatbot at http://localhost:%PORT%
"%VENV%\Scripts\python.exe" app.py
