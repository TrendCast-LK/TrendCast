"""Local disk storage for user-uploaded prediction files (thumbnails, datasets).

Served back out via the /uploads StaticFiles mount in main.py.
"""

from pathlib import Path
from uuid import uuid4

UPLOADS_DIR = Path(__file__).resolve().parent / "uploads"


def ensure_uploads_dir() -> None:
    UPLOADS_DIR.mkdir(exist_ok=True)


def save_upload(data: bytes, original_filename: str | None) -> str:
    """Saves file bytes under a random name (keeping the original extension)
    and returns the path it's served at, e.g. "/uploads/<uuid>.jpg"."""
    suffix = Path(original_filename or "").suffix
    filename = f"{uuid4().hex}{suffix}"
    (UPLOADS_DIR / filename).write_bytes(data)
    return f"/uploads/{filename}"
