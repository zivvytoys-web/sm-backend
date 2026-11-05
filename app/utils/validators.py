import re


def is_valid_phone(phone: str) -> bool:
    """Validate general phone number pattern."""
    pattern = r"^\+?[1-9]\d{1,14}$"
    return bool(re.match(pattern, phone))


def sanitize_filename(filename: str) -> str:
    """Sanitize upload filename to avoid path traversals."""
    return re.sub(r"[^\w\.-]", "_", filename)


def validate_social_handle(handle: str) -> bool:
    """Validate standard social handle without special unsafe characters."""
    pattern = r"^[a-zA-Z0-9_\.]{1,30}$"
    return bool(re.match(pattern, handle.lstrip("@")))
