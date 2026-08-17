@echo off
title EpubBackend + Cloudflare Tunnel
echo =======================================================
echo    Khoi dong EpubBackend va Cloudflare Tunnel...
echo =======================================================

echo 1. Dang chay FastAPI Backend tai cong 8000...
start /b python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

timeout /t 3 /nobreak >nul

echo 2. Dang bat Cloudflare Tunnel...
tools\cloudflared.exe tunnel --url http://127.0.0.1:8000

pause
