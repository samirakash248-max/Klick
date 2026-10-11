@echo off
title Klick - Streamlit Dashboard
cd /d "%~dp0"
call .venv\Scripts\activate.bat
echo =====================================================================
echo Starting Klick Streamlit Frontend
echo Dashboard URL: http://localhost:8501
echo =====================================================================
python -m streamlit run frontend\app.py --server.port 8501 --server.headless true
pause
