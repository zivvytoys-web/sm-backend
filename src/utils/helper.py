import re
import unicodedata
from app.core.security import (
    verify_password,
    get_password_hash,
    create_access_token,
    decode_access_token
)
from app.utils.validators import sanitize_filename


def slugify(text: str) -> str:
    """Generate a clean URL slug from text."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("utf-8")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "-", text)


def format_bytes(bytes_count: int) -> str:
    """Format file size in bytes to human-readable string."""
    if not bytes_count:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(bytes_count) < 1024.0:
            return f"{bytes_count:3.1f} {unit}"
        bytes_count /= 1024.0
    return f"{bytes_count:.1f} PB"


__all__ = [
    "verify_password",
    "get_password_hash",
    "create_access_token",
    "decode_access_token",
    "sanitize_filename",
    "slugify",
    "format_bytes"
]
