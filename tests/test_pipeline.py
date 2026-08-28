import os
import tempfile
import pytest
from unittest.mock import AsyncMock
from app.schemas.book_bible import BookBible, BookBibleDelta
from app.llm import AnthropicProvider
from app.modules.translation.application.qa_service import TranslationQualityError
from app.services import TranslationPipelineService


@pytest.mark.asyncio
async def test_pipeline_txt_translation():
    with tempfile.TemporaryDirectory() as tmpdir:
        input_txt = os.path.join(tmpdir, "input.txt")
        output_txt = os.path.join(tmpdir, "output.txt")

        with open(input_txt, "w", encoding="utf-8") as f:
            f.write("萧炎 looking at Dược Lão.\n\nHe said: Master, let's go!")

        # Mock LLM Client calls
        client = AnthropicProvider(api_key="dummy_key")
        client.extract_book_bible_delta = AsyncMock(return_value=BookBibleDelta())
        client.translate_prose_chunk = AsyncMock(return_value="Tiêu Viêm nhìn Dược Lão.\n\nHắn nói: Sư phụ, chúng ta đi thôi!")

        pipeline = TranslationPipelineService(client)
        bible = await pipeline.translate_txt_file(input_txt, output_txt)

        assert os.path.exists(output_txt)
        with open(output_txt, "r", encoding="utf-8") as f:
            content = f.read()
            assert "Tiêu Viêm nhìn Dược Lão." in content


@pytest.mark.asyncio
async def test_pipeline_does_not_publish_txt_when_cjk_remains_after_quality_gate():
    class DirtyClient:
        async def extract_book_bible_delta(self, *args, **kwargs):
            return BookBibleDelta()

        async def translate_prose_chunk(self, *args, **kwargs):
            return "Tiêu Viêm 老师 vẫn còn chữ gốc."

    with tempfile.TemporaryDirectory() as tmpdir:
        input_txt = os.path.join(tmpdir, "input.txt")
        output_txt = os.path.join(tmpdir, "output.txt")
        with open(input_txt, "w", encoding="utf-8") as file:
            file.write("萧炎 老师 chúng ta đi thôi.")

        pipeline = TranslationPipelineService(DirtyClient())
        with pytest.raises(TranslationQualityError):
            await pipeline.translate_txt_file(input_txt, output_txt)

        assert not os.path.exists(output_txt)
