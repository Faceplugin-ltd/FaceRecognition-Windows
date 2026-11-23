@echo off
setlocal
cd /d "%~dp0"
if not defined API_BASE set "API_BASE=http://127.0.0.1:8092"
if not defined DEMO_PORT set "DEMO_PORT=9092"
echo Gradio demo on port %DEMO_PORT% (API %API_BASE%)
python demo.py
exit /b %ERRORLEVEL%
