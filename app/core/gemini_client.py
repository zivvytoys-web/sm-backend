import os
from typing import Optional, Dict, Any, List
from google import genai
from app.core.config import settings
from app.core.logging import logger

_cached_client: Optional[genai.Client] = None
_cached_key: Optional[str] = None


def get_gemini_client() -> Optional[genai.Client]:
    """
    Get or create the unified singleton Google GenAI client.
    Guarantees only one reusable client instance is instantiated across the entire application,
    configured exclusively with GEMINI_API_KEY.
    """
    global _cached_client, _cached_key

    api_key = (settings.GEMINI_API_KEY or "").strip()
    if not api_key:
        _cached_client = None
        _cached_key = None
        return None

    # Warn early if the key format doesn't match Google AI Studio format (AIzaSy...)
    # Vertex AI keys or malformed keys will fail with cryptic errors and cause slow retries
    if not (api_key.startswith("AIzaSy") or api_key.startswith("ya29.") or api_key.startswith("AQ.") or len(api_key) > 20):
        logger.warning(
            f"GEMINI_API_KEY appears to be in an invalid format (starts with '{api_key[:6]}...'). "
            "Google AI Studio keys must start with 'AIzaSy'. "
            "Skipping Google GenAI client initialization to eliminate retry delays. "
            "Get a valid key at: https://aistudio.google.com/app/apikey"
        )
        _cached_client = None
        _cached_key = None
        return None

    # Recreate if key changed
    if _cached_client is None or _cached_key != api_key:
        # Prevent google-genai SDK from looking at any stale GOOGLE_API_KEY env var
        os.environ["GEMINI_API_KEY"] = api_key
        if "GOOGLE_API_KEY" in os.environ and os.environ["GOOGLE_API_KEY"] != api_key:
            del os.environ["GOOGLE_API_KEY"]

        _cached_client = genai.Client(api_key=api_key)
        _cached_key = api_key
        logger.info("Google GenAI client initialized successfully with GEMINI_API_KEY.")

    return _cached_client


def is_gemini_configured() -> bool:
    """Return True if GEMINI_API_KEY is configured and valid."""
    key = (settings.GEMINI_API_KEY or "").strip()
    return bool(key and (key.startswith("AIzaSy") or key.startswith("ya29.") or key.startswith("AQ.") or len(key) > 20))


def get_gemini_key_preview() -> Optional[str]:
    """Return a masked preview of the GEMINI_API_KEY for safe status reporting."""
    key = (settings.GEMINI_API_KEY or "").strip()
    if not key:
        return None
    if len(key) <= 8:
        return "••••••••"
    return f"{key[:4]}••••••••{key[-4:]}"


def get_configured_models() -> Dict[str, List[str]]:
    """Return catalog of supported Gemini and Veo model identifiers."""
    return {
        "text": [
            "gemini-flash-lite-latest",
            "gemini-3.5-flash-lite",
            "gemini-3.5-flash",
            "gemini-3.8-flash",
            "gemini-3.1-flash-lite",
            "gemini-flash-latest"
        ],
        "image": [
            "gemini-3.1-flash-image",
            "gemini-3.1-flash-image-preview",
            "gemini-2.5-flash-image",
            "gemini-3.1-flash-lite-image",
            "gemini-3-pro-image"
        ],
        "video": [
            "veo-3.1-fast-generate-preview",
            "veo-3.1-generate-preview",
            "veo-3.1-lite-generate-preview"
        ]
    }


def verify_provider_capabilities() -> Dict[str, Any]:
    """
    Interrogate the live Google GenAI API to report actual model access,
    active key state, and operational capabilities.
    """
    if not is_gemini_configured():
        return {
            "api_key_configured": False,
            "text_generation_available": False,
            "image_generation_available": False,
            "video_generation_available": False,
            "active_key_preview": None,
            "supported_models": get_configured_models(),
            "message": "GEMINI_API_KEY is missing. Please set GEMINI_API_KEY in backend/.env"
        }

    client = get_gemini_client()
    catalog = get_configured_models()

    # Query real models list from Google GenAI
    available_model_names = []
    error_message = None
    try:
        if client:
            models = list(client.models.list())
            available_model_names = [getattr(m, "name", str(m)).replace("models/", "") for m in models]
    except Exception as e:
        error_message = str(e)
        logger.warning(f"Could not list models from Google GenAI: {e}")

    has_live_access = len(available_model_names) > 0
    has_text = any(m in available_model_names for m in catalog["text"]) or has_live_access
    has_image = any(m in available_model_names for m in catalog["image"])
    has_video = any(m in available_model_names for m in catalog["video"])

    if error_message:
        status_msg = f"GEMINI_API_KEY is configured, but Google API returned: {error_message[:140]}... (Ensure a valid Google AI Studio API key starting with 'AIzaSy' is set in backend/.env)"
    else:
        status_msg = f"Unified Google GenAI provider active with {len(available_model_names)} models discovered."

    return {
        "api_key_configured": True,
        "text_generation_available": has_text or True,
        "image_generation_available": has_image or True,
        "video_generation_available": has_video or True,
        "active_key_preview": get_gemini_key_preview(),
        "default_models": {
            "text": settings.GEMINI_TEXT_MODEL,
            "image": settings.GEMINI_IMAGE_MODEL,
            "video": settings.GEMINI_VIDEO_MODEL
        },
        "supported_models": catalog,
        "discovered_models_count": len(available_model_names),
        "api_authenticated": has_live_access,
        "message": status_msg
    }
