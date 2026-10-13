@echo off
title AirMonitor Server
cd /d "%~dp0"

echo ============================================
echo   AirMonitor Server - startup
echo ============================================
echo.

echo [1/3] Checking Docker engine...
docker info >nul 2>&1
if %errorlevel%==0 goto engine_up

echo       Docker is not running - starting Docker Desktop...
start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
echo       Waiting for the engine (can take a minute)...

set /a tries=0
:wait_engine
set /a tries+=1
if %tries% gtr 60 goto engine_failed
timeout /t 5 /nobreak >nul
docker info >nul 2>&1
if not %errorlevel%==0 goto wait_engine

:engine_up
echo       Docker engine is up.
echo.

rem On a fresh machine the images may not be loaded yet - restore them
rem from the archive that travels with this folder.
docker image inspect airmonitor-collector:latest >nul 2>&1
if not %errorlevel%==0 (
    if exist airmonitor-images.tar (
        echo       Images not found - loading from airmonitor-images.tar...
        docker load -i airmonitor-images.tar
    )
)

echo [2/3] Starting AirMonitor containers...
docker compose up -d
if not %errorlevel%==0 goto compose_failed
echo.

echo [3/3] Current status:
docker compose ps
echo.

echo ============================================
echo   AirMonitor is running!
echo   This PC:      http://localhost:8080
echo   Colleagues:   http://%COMPUTERNAME%:8080
echo                 (or this PC's IP, see ipconfig)
echo ============================================
start "" http://localhost:8080
pause
exit /b 0

:engine_failed
echo.
echo ERROR: Docker engine did not start within 5 minutes.
echo Open Docker Desktop manually and check what it says,
echo then run this file again.
pause
exit /b 1

:compose_failed
echo.
echo ERROR: containers failed to start. Details above.
pause
exit /b 1
