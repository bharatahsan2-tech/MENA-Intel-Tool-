@echo off
REM One-click launcher for the MENA Intel Monitor (Windows).
REM Double-click this file, or run it from a terminal.
cd /d "%~dp0"
echo Starting MENA Intel Monitor...
python -m streamlit run streamlit_app.py
pause
