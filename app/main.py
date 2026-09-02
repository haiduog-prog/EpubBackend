import os
import logging
from contextlib import asynccontextmanager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.api import api_v1_router
from app.api.auth import router as auth_router
from app.modules.translation.api import recover_pending_translation_jobs
from app.modules.library.application.facade import library_service
from app.modules.library.application.epub_build_worker import start_epub_build_worker, stop_epub_build_worker
from app.modules.library.seed import seed_demo_novel_if_empty



@asynccontextmanager
async def lifespan(_app: FastAPI):
    recover_pending_translation_jobs()
    library_service.recover_import_jobs()
    start_epub_build_worker()
    if settings.seed_demo_data and settings.app_env.lower() in {"development", "dev", "local", "test"}:
        seed_demo_novel_if_empty()
    yield
    stop_epub_build_worker()


app = FastAPI(
    title="EpubBackend API",
    version="2.0.0",
    description="Backend dịch truyện thuần Việt (v2) hỗ trợ EPUB, HTML và TXT với Claude API & Gemini API Structured Outputs.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API v1 router
app.include_router(api_v1_router)
app.include_router(auth_router)
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
reader_assets_dir = os.path.join(os.path.dirname(__file__), "static", "reader-tts")
if os.path.isdir(reader_assets_dir):
    app.mount("/reader-assets", StaticFiles(directory=reader_assets_dir), name="reader-assets")


NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


@app.get("/login", response_class=HTMLResponse)
def read_login_ui():
    login_path = os.path.join(os.path.dirname(__file__), "static", "login.html")
    if os.path.exists(login_path):
        with open(login_path, "r", encoding="utf-8") as f:
            return Response(content=f.read(), media_type="text/html", headers=NO_CACHE_HEADERS)
    return "<h1>Login UI unavailable</h1>"

@app.get("/", response_class=HTMLResponse)
def read_root_ui():
    """Phục vụ màn hình Web UI Test cơ bản cho người dùng (Paste Text & Upload File)"""
    index_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return Response(content=f.read(), media_type="text/html", headers=NO_CACHE_HEADERS)
    return "<h1>EpubBackend Online</h1>"


@app.get("/reader", response_class=HTMLResponse)
def read_reader_ui():
    """Serve the standalone public reading experience."""
    reader_path = os.path.join(os.path.dirname(__file__), "static", "reader.html")
    if os.path.exists(reader_path):
        with open(reader_path, "r", encoding="utf-8") as f:
            return Response(content=f.read(), media_type="text/html", headers=NO_CACHE_HEADERS)
    return "<h1>Reader UI unavailable</h1>"
