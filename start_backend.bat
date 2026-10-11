@echo off
title Klick - Backend Server
cd /d "%~dp0"
call .venv\Scripts\activate.bat
echo =====================================================================
echo Starting Klick Backend with visible browser support
echo API URL: http://localhost:8000
echo =====================================================================
python backend\run_server.py
pause
