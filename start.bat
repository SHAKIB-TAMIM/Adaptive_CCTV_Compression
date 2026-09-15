@echo off
title AI Risk-Aware CCTV Compression
echo.
echo ======================================
echo   AI Risk-Aware CCTV Compression
echo   Adaptive ^| Risk Detection ^| Forensic
echo ======================================
echo.

REM Check prerequisites
where node >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Node.js not found!
    echo Install from: https://nodejs.org/
    pause
    exit /b 1
)

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found!
    echo Install from: https://python.org/
    pause
    exit /b 1
)

where ffmpeg >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] FFmpeg not found!
    echo Download from: https://ffmpeg.org/download.html
    echo Add to PATH after installing.
    pause
    exit /b 1
)

echo [OK] All prerequisites found.
echo.

REM Stop previous instances
echo [STOP] Stopping previous instances...
taskkill /F /IM node.exe >nul 2>&1
taskkill /F /IM ffmpeg.exe >nul 2>&1
timeout /t 1 >nul

REM Start server
echo [START] Starting server on port 5000...
cd /d "%~dp0server"
start "CCTV-Server" /min node server.js
timeout /t 4 >nul

REM Start edge-node
echo [START] Starting edge-node...
cd /d "%~dp0edge-node"
start "CCTV-Edge" /min python -u camera_manager.py --server http://127.0.0.1:5000 --config ../configs/cameras.yaml --use-optimizer
timeout /t 5 >nul

REM Start dashboard
echo [START] Starting dashboard on port 3000...
cd /d "%~dp0dashboard"
start "CCTV-Dashboard" /min npm run dev
timeout /t 3 >nul

echo.
echo ======================================
echo   System is running!
echo.
echo   Dashboard:  http://localhost:3000
echo   Settings:   http://localhost:3000/config
echo   Server API: http://localhost:5000
echo.
echo   Press any key to stop all...
echo ======================================
echo.

pause >nul

REM Cleanup
echo [STOP] Stopping all processes...
taskkill /F /IM node.exe >nul 2>&1
taskkill /F /IM ffmpeg.exe >nul 2>&1
taskkill /F /IM python.exe /FI "WINDOWTITLE eq CCTV-Edge*" >nul 2>&1
echo All stopped.
timeout /t 2 >nul
