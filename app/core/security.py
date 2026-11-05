from datetime import datetime, timedelta, timezone
from typing import Any, Union
import bcrypt
from jose import jwt, JWTError, ExpiredSignatureError
from fastapi import HTTPException, status
from app.core.config import settings


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify raw password against bcrypt hash."""
    try:
        pwd_bytes = plain_password.encode("utf-8")[:72]
        hash_bytes = hashed_password.encode("utf-8")
        return bcrypt.checkpw(pwd_bytes, hash_bytes)
    except Exception:
        return False


def get_password_hash(password: str) -> str:
    """Generate bcrypt password hash."""
    pwd_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def create_access_token(subject: Union[str, Any], expires_delta: timedelta | None = None) -> str:
    """Create signed JWT access token with user identifier and expiration."""
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode = {"exp": expire, "sub": str(subject), "iat": datetime.now(timezone.utc)}
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> dict[str, Any]:
    """
    Decode and validate a JWT access token.
    Enforces signature verification against SECRET_KEY and strictly allows only the configured ALGORITHM.
    Raises HTTPException 401 with standard WWW-Authenticate header on any failure.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials: token is invalid or malformed",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            options={"verify_signature": True, "verify_exp": True, "verify_sub": True}
        )
        return payload
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTError:
        raise credentials_exception
    except Exception:
        raise credentials_exception


def create_timed_token(subject: Union[str, Any], token_type: str, expires_delta: timedelta) -> str:
    """Create signed, tamper-proof JWT token with specific purpose/type and expiration."""
    expire = datetime.now(timezone.utc) + expires_delta
    to_encode = {
        "exp": expire,
        "sub": str(subject),
        "type": token_type,
        "iat": datetime.now(timezone.utc)
    }
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_timed_token(token: str, expected_type: str) -> dict[str, Any]:
    """Validate and decode a timed token, ensuring correct token_type."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Invalid or malformed {expected_type} token."
    )
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            options={"verify_signature": True, "verify_exp": True, "verify_sub": True}
        )
        if payload.get("type") != expected_type:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Token type mismatch: expected {expected_type}."
            )
        return payload
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{expected_type.replace('_', ' ').capitalize()} token has expired. Please request a new one."
        )
    except JWTError:
        raise credentials_exception
    except Exception:
        raise credentials_exception



def _get_fernet():
    import base64
    import hashlib
    from cryptography.fernet import Fernet
    
    key_src = getattr(settings, "ENCRYPTION_KEY", None) or settings.SECRET_KEY
    key_material = hashlib.sha256(key_src.encode("utf-8")).digest()
    fernet_key = base64.urlsafe_b64encode(key_material)
    return Fernet(fernet_key)


def encrypt_token(plain_token: str | None) -> str | None:
    """Encrypt sensitive OAuth token or secret at rest."""
    if not plain_token:
        return None
    try:
        f = _get_fernet()
        return f.encrypt(plain_token.encode("utf-8")).decode("utf-8")
    except Exception:
        return plain_token


def decrypt_token(encrypted_token: str | None) -> str | None:
    """Decrypt sensitive OAuth token or secret for provider API calls."""
    if not encrypted_token:
        return None
    try:
        f = _get_fernet()
        return f.decrypt(encrypted_token.encode("utf-8")).decode("utf-8")
    except Exception:
        return encrypted_token


def mask_secret(secret: str | None, visible_chars: int = 4) -> str:
    """Mask secret tokens for safe presentation in admin views."""
    if not secret:
        return "Not Configured"
    if len(secret) <= visible_chars:
        return "••••••••"
    return "•" * (len(secret) - visible_chars) + secret[-visible_chars:]


import re

# Match zero-width and invisible formatting characters commonly copied from formatted text
INVISIBLE_CHARS_PATTERN = re.compile(r"[\u200b\u200c\u200d\ufeff\u2060\u200e\u200f\u00a0\r\n]+")


def sanitize_credential_token(token: str | None, field_name: str = "Token", allow_empty: bool = True) -> str | None:
    """
    Sanitize and normalize credential tokens (access token, refresh token, api key/secret):
    - Strips surrounding whitespace.
    - Removes accidental zero-width/invisible characters (e.g. U+200B Zero Width Space, U+FEFF BOM).
    - Validates ASCII encodability required for HTTP Authorization headers and OAuth payloads.
    - Rejects embedded whitespace and non-ASCII characters without returning raw secret contents.
    """
    if token is None:
        return None

    cleaned = INVISIBLE_CHARS_PATTERN.sub("", str(token)).strip()

    if not cleaned:
        if not allow_empty:
            raise ValueError(f"{field_name} cannot be empty.")
        return ""

    try:
        cleaned.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError(f"{field_name} contains invalid characters. Non-ASCII characters are not permitted.")

    if " " in cleaned or "\t" in cleaned:
        raise ValueError(f"{field_name} contains invalid internal whitespace.")

    return cleaned

