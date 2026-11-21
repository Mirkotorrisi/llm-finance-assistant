"""Reading an uploaded file without trusting its size.

``await file.read()`` buffers whatever the client sends before any check runs. This reads in chunks and stops at the
cap, so a multi-GB body costs at most ``max_bytes`` of memory and is refused with 413.
"""

from fastapi import HTTPException, UploadFile

from src.services.file_processor import MAX_FILE_SIZE

CHUNK = 1024 * 1024


async def read_upload(file: UploadFile, max_bytes: int = MAX_FILE_SIZE) -> bytes:
    if file.size is not None and file.size > max_bytes:
        raise HTTPException(status_code=413, detail=f"File size exceeds maximum limit of {max_bytes // (1024 * 1024)}MB")
    parts: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail=f"File size exceeds maximum limit of {max_bytes // (1024 * 1024)}MB")
        parts.append(chunk)
    return b"".join(parts)
