import os
import logging
from contextlib import asynccontextmanager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.api import api_v1_router
from app.api.auth import router as auth_router
from app.modules.translation.api import recover_pending_translation_jobs
from app.modules.library.application.facade import library_service


@asynccontextmanager
async def lifespan(_app: FastAPI):
    recover_pending_translation_jobs()
    library_service.recover_import_jobs()
    yield

app = FastAPI(
    title="EpubBackend API",
    version="2.0.0",
    description="Backend dá»‹ch truyá»‡n thuáº§n Viá»‡t (v2) há»— trá»£ EPUB, HTML vÃ  TXT vá»›i Claude API & Gemini API Structured Outputs.",
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
app.mount("/reader-assets", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static", "reader-tts")), name="reader-assets")



@app.get("/login", response_class=HTMLResponse)
def read_login_ui():
    login_path = os.path.join(os.path.dirname(__file__), "static", "login.html")
    if os.path.exists(login_path):
        with open(login_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Login UI unavailable</h1>"

@app.get("/", response_class=HTMLResponse)
def read_root_ui():
    """Phá»¥c vá»¥ mÃ n hÃ¬nh Web UI Test cÆ¡ báº£n cho ngÆ°á»i dÃ¹ng (Paste Text & Upload File)"""
    index_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>EpubBackend Online</h1>"


@app.get("/reader", response_class=HTMLResponse)
def read_reader_ui():
    """Serve the standalone public reading experience."""
    reader_path = os.path.join(os.path.dirname(__file__), "static", "reader.html")
    if os.path.exists(reader_path):
        with open(reader_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Reader UI unavailable</h1>"


