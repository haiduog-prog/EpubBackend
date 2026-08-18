from typing import Optional
from fastapi import APIRouter, Header
from app.schemas.book_bible import BookBible
from app.schemas.translation import QAReport
from app.llm import create_llm_client
from app.services import QAService
from app.core import storage_repo

router = APIRouter(prefix="/qa", tags=["Quality Assurance"])


@router.post("/check", response_model=QAReport)
async def check_qa_endpoint(
    original_text: str,
    translated_text: str,
    job_id: Optional[str] = None,
    x_api_key: Optional[str] = Header(default=None),
    x_provider: Optional[str] = Header(default=None),
    x_model: Optional[str] = Header(default=None)
):
    bible = storage_repo.get_bible(job_id) if job_id else BookBible()
    llm_client = create_llm_client(provider=x_provider or "gemini", api_key=x_api_key, model=x_model)
    qa_service = QAService(llm_client)
    return await qa_service.verify_chunk(original_text, translated_text, bible)
