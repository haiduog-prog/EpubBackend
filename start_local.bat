@echo off
setlocal enabledelayedexpansion
title EpubBackend Local Server

:: 1. Chuyen working directory ve thu muc chua script (Project root)
cd /d "%~dp0"

echo =======================================================
echo       KHOI DONG EPUBBACKEND LOCAL ENVIRONMENT
echo =======================================================

:: 2. Tao thu muc data/ va storage/ neu chua co
echo [1/4] Kiem tra thu muc data/ va storage/...
if not exist "data" (
    mkdir "data"
)
if not exist "storage" (
    mkdir "storage"
)

:: 3. Gan bien moi truong cho rieng process hien tai (khong ghi de .env)
echo [2/4] Thiet lap bien moi truong local...
set "APP_ENV=local"
set "AUTH_REQUIRED=false"
set "DATABASE_URL=sqlite:///./data/local_db.sqlite3"
set "STORAGE_PROVIDER=local"
set "STRUCTURED_STORAGE_BACKEND=postgres"
set "STRUCTURED_STORAGE_READ_SOURCE=postgres"
set "GOOGLE_DRIVE_SYNC_ENABLED=true"
set "GOOGLE_DRIVE_SYNC_FOLDER_ID=1DhlKSSi768LGzIgpFxlqrMm97OM5X_4K"
set "GOOGLE_DRIVE_CREDENTIALS_FILE=%~dp0google-credentials.json"

:: 4. Uu tien dung virtual environment (.venv) neu co
set "PYTHON_EXE=python"
if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
    echo       -^> Su dung Python tu .venv [!PYTHON_EXE!]
) else (
    echo       -^> Su dung Python tu PATH system
)

:: 5. Chay Alembic migrations de cap nhat schema SQLite
echo [3/4] Chay Alembic migrations len SQLite...
"%PYTHON_EXE%" -m alembic upgrade head
if errorlevel 1 (
    echo [ERROR] Alembic migration that bai voi ma loi !errorlevel!! Huy khoi dong.
    pause
    exit /b !errorlevel!
)

:: 6. Khoi chay FastAPI server voi Uvicorn tren 127.0.0.1:8000
echo [4/4] Khoi dong FastAPI Backend tai http://127.0.0.1:8000...
echo =======================================================
echo Web UI Test : http://127.0.0.1:8000/
echo Reader UI   : http://127.0.0.1:8000/reader
echo API Docs    : http://127.0.0.1:8000/docs
echo Nhan Ctrl+C de dung server.
echo =======================================================

"%PYTHON_EXE%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
