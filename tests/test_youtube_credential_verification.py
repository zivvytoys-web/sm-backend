import sys
import os
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.security import sanitize_credential_token
from app.integrations.social.youtube import YouTubeClient
from app.services.youtube_token_manager import YouTubeAuthError, YouTubeAPIError
from app.api.routes.social_accounts import ConnectCredentialsRequest
from app.utils.enums import PlatformType


# =========================================================================
# 1. UNIT TESTS: sanitize_credential_token
# =========================================================================

def test_sanitize_clean_token():
    clean = "ya29.a0AcM6123456789_clean-token.example"
    result = sanitize_credential_token(clean, "YouTube access token")
    assert result == clean


def test_sanitize_token_with_u200b_zero_width_space():
    # Emulate the exact error reported by user: \u200b in position 359
    base_token = "ya29.a0AcM6123456789_abcXYZ"
    token_with_zwsp = f"{base_token}\u200b"
    result = sanitize_credential_token(token_with_zwsp, "YouTube access token")
    assert result == base_token
    assert "\u200b" not in result
    # Verify ASCII encoding succeeds without error
    result.encode("ascii")


def test_sanitize_token_with_multiple_invisible_unicode_chars():
    # Tokens copied with BOM (\ufeff), zero-width non-joiner (\u200c), and \u200b
    token = "\ufeffya29.token\u200bmiddle\u200c_end\u200d\u2060"
    result = sanitize_credential_token(token, "YouTube access token")
    assert result == "ya29.tokenmiddle_end"
    result.encode("ascii")


def test_sanitize_token_with_surrounding_whitespace_and_newlines():
    token = "  \r\n\t  1//04_refresh_token_xyz123   \n  "
    result = sanitize_credential_token(token, "YouTube refresh token")
    assert result == "1//04_refresh_token_xyz123"


def test_sanitize_empty_token_behavior():
    # When allow_empty=True, returns empty string
    assert sanitize_credential_token("", allow_empty=True) == ""
    assert sanitize_credential_token("   ", allow_empty=True) == ""
    assert sanitize_credential_token("\u200b\u200b", allow_empty=True) == ""

    # When allow_empty=False, raises ValueError without exposing secrets
    with pytest.raises(ValueError) as excinfo:
        sanitize_credential_token("", "Access token", allow_empty=False)
    assert "Access token cannot be empty" in str(excinfo.value)

    with pytest.raises(ValueError) as excinfo:
        sanitize_credential_token("   \u200b  ", "Refresh token", allow_empty=False)
    assert "Refresh token cannot be empty" in str(excinfo.value)


def test_sanitize_rejects_embedded_whitespace():
    # Embedded whitespace is invalid for OAuth tokens
    with pytest.raises(ValueError) as excinfo:
        sanitize_credential_token("ya29.token with space", "Access token")
    assert "invalid internal whitespace" in str(excinfo.value)


def test_sanitize_rejects_invalid_non_ascii_characters():
    # Foreign characters that cannot be encoded in ASCII HTTP headers
    with pytest.raises(ValueError) as excinfo:
        sanitize_credential_token("ya29.token_with_中文_chinese", "Access token")
    assert "Non-ASCII characters are not permitted" in str(excinfo.value)
    # Ensure raw token is NEVER leaked in the exception message
    assert "中文" not in str(excinfo.value)


# =========================================================================
# 2. CLIENT TESTS: YouTubeClient with Normalized Tokens
# =========================================================================

@pytest.mark.asyncio
async def test_youtube_client_verifies_token_with_u200b():
    # Token containing \u200b zero-width space
    dirty_token = "ya29.mock_valid_token_12345\u200b"
    client = YouTubeClient(access_token=dirty_token)

    # Mock response from YouTube channels API
    mock_response = httpx.Response(
        200,
        json={
            "items": [
                {
                    "id": "UC_test_channel_id",
                    "snippet": {
                        "title": "My Test YouTube Channel",
                        "customUrl": "@testchannel"
                    }
                }
            ]
        },
        request=httpx.Request("GET", "https://www.googleapis.com/youtube/v3/channels")
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        details = await client.verify_and_fetch_details()

        assert details["valid"] is True
        assert details["account_id"] == "UC_test_channel_id"
        assert details["account_name"] == "@testchannel"
        # Verify Authorization header sent was strictly clean ASCII without \u200b
        call_headers = mock_get.call_args[1]["headers"]
        assert call_headers["Authorization"] == "Bearer ya29.mock_valid_token_12345"
        assert "\u200b" not in call_headers["Authorization"]


@pytest.mark.asyncio
async def test_youtube_client_rejects_empty_token():
    client = YouTubeClient(access_token="")
    with pytest.raises(YouTubeAuthError) as excinfo:
        await client.verify_and_fetch_details()
    assert "requires an active Google OAuth" in str(excinfo.value)


@pytest.mark.asyncio
async def test_youtube_client_rejects_api_key_cleanly():
    # Google API Key starts with AIza
    client = YouTubeClient(access_token="  \u200bAIzaSyMockGoogleCloudApiKey123  ")
    with pytest.raises(YouTubeAuthError) as excinfo:
        await client.verify_and_fetch_details()
    assert "Google Cloud API Key" in str(excinfo.value)


@pytest.mark.asyncio
async def test_youtube_client_handles_unencodable_chars_without_traceback():
    # Emulate an unencodable character slipping to httpx call
    client = YouTubeClient(access_token="ya29.valid_token")

    with patch("httpx.AsyncClient.get", side_effect=UnicodeEncodeError("ascii", "\u200b", 0, 1, "ordinal not in range")):
        with pytest.raises(YouTubeAuthError) as excinfo:
            await client.verify_and_fetch_details()
        assert "credentials contain invalid non-ascii characters" in str(excinfo.value).lower()


# =========================================================================
# 3. SCHEMA VALIDATION TESTS: ConnectCredentialsRequest
# =========================================================================

def test_connect_credentials_request_sanitizes_u200b_in_tokens():
    req = ConnectCredentialsRequest(
        platform=PlatformType.YOUTUBE,
        account_name="My Channel",
        access_token="ya29.token_with_\u200b_zwsp",
        refresh_token="1//04_refresh_\u200b_zwsp"
    )
    assert "\u200b" not in req.access_token
    assert req.access_token == "ya29.token_with__zwsp"
    assert "\u200b" not in req.refresh_token
    assert req.refresh_token == "1//04_refresh__zwsp"


def test_connect_credentials_request_trims_whitespace():
    req = ConnectCredentialsRequest(
        platform=PlatformType.YOUTUBE,
        account_name="My Channel",
        access_token="   ya29.surrounded_token\r\n  ",
        refresh_token="  \t1//04.refresh   "
    )
    assert req.access_token == "ya29.surrounded_token"
    assert req.refresh_token == "1//04.refresh"


def test_connect_credentials_request_rejects_non_ascii_cleanly():
    with pytest.raises(Exception) as excinfo:
        ConnectCredentialsRequest(
            platform=PlatformType.YOUTUBE,
            account_name="My Channel",
            access_token="ya29.token_with_русский_russian"
        )
    # The user-facing error message must report the non-ASCII validation error
    error_msg = excinfo.value.errors()[0]["msg"]
    assert "Non-ASCII characters are not permitted" in error_msg
    # Token value must not be exposed in the error message
    assert "русский" not in error_msg
