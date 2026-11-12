@echo off
setlocal
cd /d "%~dp0"

if not exist "%~dp0lib\cpu\FaceRecognitionSDK.dll" goto :need_lib
if not exist "%~dp0lib\cpu\facerec.dll" goto :need_lib
if not exist "%~dp0lib\cpu\far.fpk" goto :need_lib
if not exist "%~dp0lib\cpu\fal-eng.dll" goto :need_lib
if not exist "%~dp0lib\cpu\fal.fpk" goto :need_lib
goto :ready

:need_lib
echo ERROR: .\lib\cpu\ is empty.
echo.
echo Download all files from Google Drive into .\lib\cpu\:
echo   https://drive.google.com/drive/folders/1kwGZ1TYO3VtwSI2xkRH9p0HtnbRfknyU?usp=sharing
echo.
echo Need:
echo   .\lib\cpu\FaceRecognitionSDK.dll
echo   .\lib\cpu\facerec.dll
echo   .\lib\cpu\far.fpk
echo   .\lib\cpu\fal-eng.dll
echo   .\lib\cpu\fal.fpk
echo.
exit /b 1

:ready
if not defined LICENSE set "LICENSE=%~dp0license.txt"
if not defined PORT set "PORT=8083"
set "PATH=%~dp0lib\cpu;%PATH%"

echo Starting Face Recognition SDK API on port %PORT% ...
python app.py
exit /b %ERRORLEVEL%
