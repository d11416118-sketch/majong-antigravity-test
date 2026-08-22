@echo off
setlocal
cd /d "%~dp0"

where docker >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Docker is not installed or is not in PATH.
    echo Install Docker Desktop, then run this file again.
    pause
    exit /b 1
)

docker compose version >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Docker Compose is unavailable.
    echo Update Docker Desktop, then run this file again.
    pause
    exit /b 1
)

docker info >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Docker Desktop is not running.
    echo Start Docker Desktop and wait until the engine is ready.
    pause
    exit /b 1
)

if not defined PORT set "PORT=5001"
echo Starting Mahjong with Docker on http://localhost:%PORT%/
echo The first build can take several minutes. Please keep this window open.
docker compose up --build -d --wait
if errorlevel 1 (
    echo [ERROR] The game container did not become ready.
    docker compose logs --tail 80
    pause
    exit /b 1
)

echo Mahjong is ready: http://localhost:%PORT%/
echo Use stop_game.bat when you want to stop the server.
start "" "http://localhost:%PORT%/"
pause
endlocal
