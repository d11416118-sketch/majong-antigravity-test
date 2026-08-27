@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "PYTHON_EXE="

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import flask, flask_socketio" >nul 2>nul
    if not errorlevel 1 set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
)

if not defined PYTHON_EXE (
    call python -c "import flask, flask_socketio" >nul 2>nul
    if not errorlevel 1 set "PYTHON_EXE=python"
)

if not defined PYTHON_EXE if exist "%USERPROFILE%\.pyenv\pyenv-win\versions\3.10.9\python.exe" (
    "%USERPROFILE%\.pyenv\pyenv-win\versions\3.10.9\python.exe" -c "import flask, flask_socketio" >nul 2>nul
    if not errorlevel 1 set "PYTHON_EXE=%USERPROFILE%\.pyenv\pyenv-win\versions\3.10.9\python.exe"
)

if not defined PYTHON_EXE (
    echo [ERROR] No usable Python environment was found.
    echo Install Python 3.10 or newer and run: python -m pip install -r requirements.txt
    pause
    exit /b 1
)

if not defined PORT set "PORT=5001"
set "GAME_URL=http://127.0.0.1:%PORT%/"

powershell.exe -NoProfile -Command "$ProgressPreference='SilentlyContinue'; try { $response=Invoke-WebRequest -UseBasicParsing -Uri $env:GAME_URL -TimeoutSec 3; if($response.StatusCode -eq 200 -and $response.Content.Contains('static/js/socket.io.js')) { exit 0 } } catch {}; exit 1" >nul 2>nul
if not errorlevel 1 (
    echo Mahjong is already running: %GAME_URL%
    start "" "%GAME_URL%"
    exit /b 0
)

echo Starting Mahjong locally with: !PYTHON_EXE!
echo Game URL: %GAME_URL%
echo Keep this window open while playing. Press Ctrl+C to stop the server.

start "" /b powershell.exe -NoProfile -WindowStyle Hidden -Command "$ProgressPreference='SilentlyContinue'; $url=$env:GAME_URL; foreach($attempt in 1..60) { try { $response=Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 2; if($response.StatusCode -eq 200 -and $response.Content.Contains('static/js/socket.io.js')) { Start-Process $url; break } } catch {}; Start-Sleep -Seconds 1 }" >nul 2>nul

call "!PYTHON_EXE!" run_server.py
set "SERVER_EXIT=!ERRORLEVEL!"

if not "!SERVER_EXIT!"=="0" (
    echo.
    echo [ERROR] The Mahjong server stopped with exit code !SERVER_EXIT!.
    pause
)

exit /b !SERVER_EXIT!
