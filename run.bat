@echo off
setlocal
cd /d "%~dp0"

if not exist "%~dp0lib\cpu\FaceRecognitionSDK.dll" goto :need_lib
if not exist "%~dp0lib\cpu\far-eng.dll" goto :need_lib
if not exist "%~dp0lib\cpu\far.fpk" goto :need_lib
if not exist "%~dp0lib\cpu\farsec.dll" goto :need_lib
goto :ready

:need_lib
echo ERROR: .\lib\cpu\ is empty.
echo.
echo Download all files from Google Drive into .\lib\cpu\:
echo   https://drive.google.com/drive/folders/12i5d2-TahuJumre2EVYqWO8cIi_unTBz
echo.
echo Need:
echo   .\lib\cpu\FaceRecognitionSDK.dll
echo   .\lib\cpu\far-eng.dll
echo   .\lib\cpu\farsec.dll
echo   .\lib\cpu\far.fpk
echo.
exit /b 1

:ready
if not defined LICENSE set "LICENSE=%~dp0license.txt"
if not defined PORT set "PORT=8083"
set "PATH=%~dp0lib\cpu;%PATH%"

echo Starting Face Recognition API on port %PORT% ...
python app.py
exit /b %ERRORLEVEL%
