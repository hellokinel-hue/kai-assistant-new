@echo off
title KAI Assistant Launcher
color 0b
echo ========================================
echo         STARTING KAI ASSISTANT
echo ========================================
echo.
echo Installing/checking dependencies...
python -m pip install -r requirements.txt >nul 2>&1
echo.
echo Launching KAI web application...
python -m streamlit run kai.py
pause