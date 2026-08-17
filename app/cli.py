import os
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
import sys
import asyncio
import argparse
from app.llm import create_llm_client
from app.services import TranslationPipelineService
from app.schemas.translation import InputType


def main():
    parser = argparse.ArgumentParser(description="EpubBackend CLI (v2) - Dá»‹ch truyá»‡n thuáº§n Viá»‡t báº±ng AI")
    parser.add_argument("command", choices=["translate"], help="Lá»‡nh thá»±c thi")
    parser.add_argument("file", help="ÄÆ°á»ng dáº«n tá»›i file .txt hoáº·c .epub cáº§n dá»‹ch")
    parser.add_argument("-o", "--output", help="ÄÆ°á»ng dáº«n file Ä‘áº§u ra", default=None)
    parser.add_argument("--provider", choices=["anthropic", "gemini"], default="anthropic", help="LLM Provider")
    parser.add_argument("--api-key", help="API Key (náº¿u khÃ´ng set env)", default=None)
    parser.add_argument("--model", help="TÃªn model cá»¥ thá»ƒ", default=None)

    args = parser.parse_args()

    input_path = os.path.abspath(args.file)
    if not os.path.exists(input_path):
        print(f"[Lá»–I] File khÃ´ng tá»“n táº¡i: {input_path}")
        sys.exit(1)

    ext = os.path.splitext(input_path)[1].lower()
    if ext == ".txt":
        input_type = InputType.TXT
    elif ext == ".epub":
        input_type = InputType.EPUB
    else:
        print("[Lá»–I] Chá»‰ há»— trá»£ Ä‘á»‹nh dáº¡ng file .txt hoáº·c .epub")
        sys.exit(1)

    output_path = args.output
    if not output_path:
        dir_name, base_name = os.path.split(input_path)
        output_path = os.path.join(dir_name, f"dich_{base_name}")

    print(f"=== EPUBBACKEND NOVEL TRANSLATOR v2 ===")
    print(f"Provider: {args.provider}")
    print(f"File Ä‘áº§u vÃ o: {input_path}")
    print(f"File Ä‘áº§u ra: {output_path}")

    llm_client = create_llm_client(provider=args.provider, api_key=args.api_key, model=args.model)
    pipeline = TranslationPipelineService(llm_client)

    def print_progress(pct: float, step: str):
        print(f"[{pct:5.1f}%] {step}")

    async def run():
        if input_type == InputType.TXT:
            bible = await pipeline.translate_txt_file(input_path, output_path, progress_callback=print_progress)
        else:
            bible = await pipeline.translate_epub_file(input_path, output_path, progress_callback=print_progress)

        print("\n=== HOÃ€N Táº¤T Báº¢N Dá»ŠCH ===")
        print(f"File Ä‘Ã£ Ä‘Æ°á»£c lÆ°u táº¡i: {output_path}")
        print(f"Sá»‘ lÆ°á»£ng nhÃ¢n váº­t trong Book Bible: {len(bible.characters)}")
        print(f"Sá»‘ lÆ°á»£ng thuáº­t ngá»¯: {len(bible.terms)}")

    asyncio.run(run())


if __name__ == "__main__":
    main()


