import os
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from app.api import api_v1_router

app = FastAPI(
    title="EpubBackend API",
    version="2.0.0",
    description="Backend dá»‹ch truyá»‡n thuáº§n Viá»‡t (v2) há»— trá»£ EPUB, HTML vÃ  TXT vá»›i Claude API & Gemini API Structured Outputs."
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API v1 router
app.include_router(api_v1_router)


@app.get("/", response_class=HTMLResponse)
def read_root_ui():
    """Phá»¥c vá»¥ mÃ n hÃ¬nh Web UI Test cÆ¡ báº£n cho ngÆ°á»i dÃ¹ng (Paste Text & Upload File)"""
    index_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>EpubBackend Online</h1>"


