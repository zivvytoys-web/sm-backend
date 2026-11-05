import pytest
import uuid
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.models.organization import Organization
from app.models.user import User
from app.models.brand_profile import BrandProfile
from app.services.gemini_service import GeminiService
from app.core.security import create_access_token, get_password_hash
from app.utils.enums import UserRole, GenerationType, JobStatus, PlatformType


@pytest.fixture
def auth_headers(db_session):
    uid = uuid.uuid4().hex[:6]
    org = Organization(name=f"Test Organization {uid}", slug=f"test-org-{uid}")
    db_session.add(org)
    db_session.flush()

    user = User(
        email=f"test_gemini_{uid}@example.com",
        hashed_password=get_password_hash("password123"),
        full_name="Gemini Tester",
        role=UserRole.ADMIN,
        is_active=True,
        organization_id=org.id
    )
    db_session.add(user)

    brand = BrandProfile(
        name="aminoid",
        industry="Fashion",
        tone_of_voice="Engaging & Sharp",
        key_themes=["linen shirts", "modern apparel"],
        hashtags=["#aminoid", "#Fashion"],
        organization_id=org.id
    )
    db_session.add(brand)
    db_session.commit()
    db_session.refresh(user)

    token = create_access_token(subject=user.id)
    return {"Authorization": f"Bearer {token}"}


def test_gemini_status_check(client, auth_headers):
    response = client.get("/api/v1/generations/gemini/status", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "text_generation_available" in data
    assert "supported_models" in data
    assert "gemini-3.5-flash" in data["supported_models"]["text"]
    assert "gemini-2.5-flash-image" in data["supported_models"]["image"]


def test_auto_prompt_from_brand(client, auth_headers):
    payload = {
        "campaign_goal": "product_launch",
        "target_platform": "instagram"
    }
    response = client.post("/api/v1/generations/gemini/auto-prompt", json=payload, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "text_prompt" in data
    assert "image_prompt" in data
    assert "video_prompt" in data
    assert len(data["text_prompt"]) > 5


def test_gemini_text_generation_mocked(client, auth_headers):
    mock_result = {
        "headline": "Unlock Your Brand Potential",
        "caption": "✨ Fresh styles for the new season! #Fashion #Trend",
        "hashtags": ["#Fashion", "#Style", "#Trend"],
        "call_to_action": "Shop now at the link in bio!",
        "variations": [
            {"title": "Angle 1", "hook": "Look sharp today", "body": "Quality outfits engineered for comfort."}
        ],
        "marketing_takeaways": ["Post at 6 PM", "Use high-res visuals"],
        "script_scenes": [],
        "model_name": "gemini-3.5-flash",
        "tokens_used": 180,
        "generated_at": "2026-10-10T12:00:00"
    }

    with patch.object(GeminiService, "generate_text", return_value=mock_result):
        payload = {
            "prompt": "Summer linen shirts collection launch",
            "target_platforms": ["instagram", "facebook"],
            "content_type": "caption",
            "tone": "Engaging",
            "language": "English",
            "model_name": "gemini-3.5-flash"
        }
        response = client.post("/api/v1/generations/gemini/text", json=payload, headers=auth_headers)
        assert response.status_code == 201
        data = response.json()
        assert data["job_type"] == GenerationType.TEXT_CAPTION.value
        assert data["status"] == JobStatus.COMPLETED.value
        assert data["result_data"]["caption"] == mock_result["caption"]
        assert len(data["result_data"]["hashtags"]) == 3


def test_gemini_image_generation(client, auth_headers):
    payload = {
        "prompt": "Minimalist linen shirt flatlay",
        "aspect_ratio": "1:1",
        "style_preset": "modern_product"
    }
    response = client.post("/api/v1/generations/gemini/image", json=payload, headers=auth_headers)
    assert response.status_code == 201
    data = response.json()
    assert data["job_type"] == GenerationType.IMAGE_GENERATION.value
    assert data["status"] == JobStatus.COMPLETED.value
    assert "image_url" in data["result_data"]
    assert data["result_data"]["image_url"].startswith("/uploads/media/ai_images/")


def test_generation_history_and_job_detail(client, auth_headers):
    res = client.get("/api/v1/generations/history", headers=auth_headers)
    assert res.status_code == 200
    items = res.json()
    assert isinstance(items, list)
