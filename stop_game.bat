@echo off
setlocal
cd /d "%~dp0"

where docker >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Docker is not installed or is not in PATH.
    pause
    exit /b 1
)

docker compose down
if errorlevel 1 (
    echo [ERROR] Could not stop the Mahjong containers.
    pause
    exit /b 1
)

echo Mahjong server stopped. The database remains in the data folder.
pause
endlocal
