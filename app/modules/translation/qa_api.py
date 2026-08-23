from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException
from app.schemas.book_bible import BookBible
from app.schemas.translation import QAReport
from app.config import settings
from app.llm import create_llm_client
from app.modules.translation.application.qa_service import QAService
from app.infrastructure.storage.facade import storage_repo
from app.api.dependencies import require_write_access

router = APIRouter(prefix="/qa", tags=["Quality Assurance"])


@router.post("/check", response_model=QAReport)
async def check_qa_endpoint(
    original_text: str,
    translated_text: str,
    job_id: Optional[str] = None,
    x_api_key: Optional[str] = Header(default=None),
    x_provider: Optional[str] = Header(default=None),
    x_model: Optional[str] = Header(default=None),
    _: None = Depends(require_write_access),
):
    if (
        len(original_text) > settings.max_text_input_chars
        or len(translated_text) > settings.max_text_input_chars
    ):
        raise HTTPException(status_code=413, detail="Van ban QA vuot qua gioi han cho phep.")
    bible = storage_repo.get_bible(job_id) if job_id else BookBible()
    llm_client = create_llm_client(provider=x_provider or "gemini", api_key=x_api_key, model=x_model)
    qa_service = QAService(llm_client)
    return await qa_service.verify_chunk(original_text, translated_text, bible)
