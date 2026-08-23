"""Compatibility exports for services moved into bounded contexts."""

__all__ = ["BookBibleService", "QAService", "TranslationPipelineService"]


def __getattr__(name: str):
    if name == "BookBibleService":
        from app.services.book_bible_service import BookBibleService

        return BookBibleService
    if name == "QAService":
        from app.services.qa_service import QAService

        return QAService
    if name == "TranslationPipelineService":
        from app.services.pipeline_service import TranslationPipelineService

        return TranslationPipelineService
    raise AttributeError(name)
