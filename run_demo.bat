@echo off
setlocal
cd /d "%~dp0"
if not defined API_BASE set "API_BASE=http://127.0.0.1:8083"
if not defined DEMO_PORT set "DEMO_PORT=9003"
echo Open the Gradio demo against %API_BASE% (start run.bat first).
python demo.py
exit /b %ERRORLEVEL%
