"""Bounded upload readers shared by multipart API endpoints."""

from fastapi import HTTPException, UploadFile

from app.config import settings


async def read_upload_limited(file: UploadFile, limit: int | None = None) -> bytes:
    max_bytes = limit or settings.max_upload_bytes
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File vượt quá giới hạn {max_bytes // (1024 * 1024)} MB.",
            )
        chunks.append(chunk)
    return b"".join(chunks)
