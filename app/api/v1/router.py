from fastapi import APIRouter, Depends

from app.auth import get_current_user
from app.api.v1.translate import router as translate_router
from app.api.v1.book_bible import router as book_bible_router
from app.api.v1.qa import router as qa_router
from app.platform.admin.api import router as admin_router
from app.api.v1.character_profiles import router as character_profiles_router
from app.api.v1.library import router as library_router
from app.api.v1.reader import router as reader_router

# Every v1 endpoint is private; nested routers inherit this dependency.
api_v1_router = APIRouter(prefix="/api/v1", dependencies=[Depends(get_current_user)])
api_v1_router.include_router(translate_router)
api_v1_router.include_router(character_profiles_router)
api_v1_router.include_router(book_bible_router)
api_v1_router.include_router(qa_router)
api_v1_router.include_router(admin_router)
api_v1_router.include_router(library_router)
api_v1_router.include_router(reader_router)
