"""Backward-compatible imports for the translation bounded context."""

from app.llm.base import BaseLLMClient
from app.modules.book_bible.application.facade import BookBibleService
from app.modules.book_bible.domain.address_resolver import AddressRuleResolver
from app.modules.book_bible.domain.review_policy import HybridPolicyEngine
from app.modules.translation.application.facade import TranslationPipelineService
from app.parsers.epub_parser import EPUBParser
from app.parsers.txt_chunker import TXTChunker
from app.services.qa_service import QAService

__all__ = [
    "AddressRuleResolver",
    "BaseLLMClient",
    "BookBibleService",
    "EPUBParser",
    "HybridPolicyEngine",
    "QAService",
    "TXTChunker",
    "TranslationPipelineService",
]
