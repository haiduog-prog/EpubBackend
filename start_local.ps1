# ==============================================================================
# Script khoi dong EpubBackend o moi truong Local (100% doc lap khong Render/Supabase)
# ==============================================================================
$ErrorActionPreference = "Stop"

# 1. Chuyen working directory ve thu muc chua script (Project root)
$ProjectRoot = $PSScriptRoot
Set-Location $ProjectRoot

Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host "      KHOI DONG EPUBBACKEND LOCAL ENVIRONMENT          " -ForegroundColor Cyan
Write-Host "=======================================================" -ForegroundColor Cyan

# 2. Tao thu muc data/ va storage/ neu chua co
if (-not (Test-Path "$ProjectRoot\data")) {
    Write-Host "[1/4] Tao thu muc data/..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Path "$ProjectRoot\data" | Out-Null
}
if (-not (Test-Path "$ProjectRoot\storage")) {
    Write-Host "[1/4] Tao thu muc storage/..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Path "$ProjectRoot\storage" | Out-Null
}

# 3. Gan bien moi truong cho rieng process hien tai (khong ghi de .env)
Write-Host "[2/4] Thiet lap bien moi truong local..." -ForegroundColor Yellow
$env:APP_ENV = "local"
$env:AUTH_REQUIRED = "false"
$env:DATABASE_URL = "sqlite:///./data/local_db.sqlite3"
$env:STORAGE_PROVIDER = "local"
$env:STRUCTURED_STORAGE_BACKEND = "postgres"
$env:STRUCTURED_STORAGE_READ_SOURCE = "postgres"
$env:GOOGLE_DRIVE_SYNC_ENABLED = "true"
$env:GOOGLE_DRIVE_SYNC_FOLDER_ID = "1DhlKSSi768LGzIgpFxlqrMm97OM5X_4K"
$env:GOOGLE_DRIVE_CREDENTIALS_FILE = "$ProjectRoot\google-credentials.json"

# 4. Uu tien dung virtual environment (.venv) neu co
$PythonExe = "python"
if (Test-Path "$ProjectRoot\.venv\Scripts\python.exe") {
    $PythonExe = "$ProjectRoot\.venv\Scripts\python.exe"
    Write-Host "      -> Su dung Python tu .venv ($PythonExe)" -ForegroundColor Gray
} else {
    Write-Host "      -> Su dung Python tu PATH system" -ForegroundColor Gray
}

# 5. Chay Alembic migrations de cap nhat schema SQLite
Write-Host "[3/4] Chay Alembic migrations len SQLite..." -ForegroundColor Yellow
& $PythonExe -m alembic upgrade head
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Alembic migration that bai voi exit code $LASTEXITCODE! Huy khoi dong." -ForegroundColor Red
    exit $LASTEXITCODE
}

# 6. Khoi chay FastAPI server voi Uvicorn tren 127.0.0.1:8000
Write-Host "[4/4] Khoi dong FastAPI Backend tai http://127.0.0.1:8000..." -ForegroundColor Green
Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host "Web UI Test : http://127.0.0.1:8000/" -ForegroundColor Green
Write-Host "Reader UI   : http://127.0.0.1:8000/reader" -ForegroundColor Green
Write-Host "API Docs    : http://127.0.0.1:8000/docs" -ForegroundColor Green
Write-Host "Nhan Ctrl+C de dung server." -ForegroundColor Gray
Write-Host "=======================================================" -ForegroundColor Cyan

& $PythonExe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
