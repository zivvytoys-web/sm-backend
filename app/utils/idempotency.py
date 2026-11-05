import hashlib
import uuid


def generate_idempotency_key(prefix: str = "req") -> str:
    """Generate a unique idempotency key."""
    return f"{prefix}_{uuid.uuid4().hex}"


def hash_payload(data: str) -> str:
    """Hash a string payload using SHA256."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
