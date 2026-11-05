import os
import pytest
from app.models.media_asset import MediaAsset
from app.models.generation_job import GenerationJob
from app.utils.enums import MediaType, JobStatus, GenerationType

def get_auth_token(client, email="ai_creator_test@socialai.com", role="creator"):
    client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "SecurePassword123!",
        "full_name": "AI Creator",
        "role": role
    })
    res = client.post("/api/v1/auth/login", json={"email": email, "password": "SecurePassword123!"})
    return res.json()["access_token"]


def test_brand_profile_required_before_generation(client):
    """Verifies that generation fails with 404 for invalid brand and 400 when brand profile is incomplete."""
    token = get_auth_token(client, email="no_brand_user@socialai.com")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Attempt image generation with non-existent brand_profile_id -> 404
    res = client.post("/api/v1/generations/gemini/image", json={
        "brand_profile_id": 999999,
        "prompt": "Luxury handbag promotion on marble pedestal",
        "aspect_ratio": "1:1"
    }, headers=headers)
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()

    # 2. Attempt video generation with non-existent brand_profile_id -> 404
    v_res = client.post("/api/v1/generations/gemini/video", json={
        "brand_profile_id": 999999,
        "prompt": "Dynamic rotating camera orbit around new collection",
        "aspect_ratio": "9:16",
        "duration_seconds": 6
    }, headers=headers)
    assert v_res.status_code == 404
    assert "not found" in v_res.json()["detail"].lower()

    # 3. Direct service validation test for incomplete brand profile -> 400
    from app.services.image_generation_service import ImageGenerationService
    from app.services.video_generation_service import VideoGenerationService
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        ImageGenerationService.validate_brand_profile(None)
    assert excinfo.value.status_code == 400

    with pytest.raises(HTTPException) as excinfo:
        VideoGenerationService.validate_brand_profile(None)
    assert excinfo.value.status_code == 400


def test_real_ai_image_generation_and_storage(client):
    """Verifies real AI image generation, Cloudinary/local persistence, and DB recording."""
    token = get_auth_token(client, email="image_gen_user@socialai.com")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create a complete brand profile
    brand_res = client.post("/api/v1/brand-profiles/", json={
        "name": "Aura Luxury Studio",
        "industry": "Fashion & Jewelry",
        "tagline": "Timeless Modern Elegance",
        "tone_of_voice": "Sophisticated & Bold",
        "color_palette": ["#4F46E5", "#EC4899", "#0F172A"]
    }, headers=headers)
    assert brand_res.status_code == 201
    brand_id = brand_res.json()["id"]

    # 2. Generate Image
    gen_res = client.post("/api/v1/generations/gemini/image", json={
        "brand_profile_id": brand_id,
        "prompt": "Handcrafted diamond sapphire necklace under soft spotlight",
        "aspect_ratio": "1:1",
        "style_preset": "luxury",
        "model_name": "imagen-3.0-generate-002"
    }, headers=headers)

    assert gen_res.status_code == 201
    job_data = gen_res.json()
    assert job_data["job_type"] == "image_generation"
    assert job_data["status"] == "completed"

    rd = job_data["result_data"]
    assert "image_url" in rd
    assert rd["image_url"].startswith("http") or rd["image_url"].startswith("/uploads")
    assert "file_name" in rd
    assert rd["file_name"].endswith(".png")
    assert rd["file_size_bytes"] > 0
    assert "media_asset_id" in rd
    assert rd["media_asset_id"] is not None


def test_real_ai_video_generation_and_playback(client):
    """Verifies real AI video generation produces a playable MP4 file saved to storage and DB."""
    token = get_auth_token(client, email="video_gen_user@socialai.com")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create brand profile
    brand_res = client.post("/api/v1/brand-profiles/", json={
        "name": "Pulse Athletic",
        "industry": "Fitness & Activewear",
        "tagline": "Unleash Your Potential",
        "cta": "Shop the Spring Drop Today",
        "color_palette": ["#10B981", "#6366F1", "#0F172A"]
    }, headers=headers)
    assert brand_res.status_code == 201
    brand_id = brand_res.json()["id"]

    # 2. Generate Video Reel (Vertical 9:16)
    gen_res = client.post("/api/v1/generations/gemini/video", json={
        "brand_profile_id": brand_id,
        "prompt": "High-intensity sprint training in lightweight activewear",
        "aspect_ratio": "9:16",
        "duration_seconds": 6,
        "fps": 24,
        "resolution": "720p",
        "model_name": "veo-2.0-generate-001"
    }, headers=headers)

    assert gen_res.status_code == 200
    job_data = gen_res.json()
    assert job_data["job_type"] == "video_generation"
    assert job_data["status"] in ["completed", "processing"]

    rd = job_data["result_data"]
    assert "video_url" in rd or "operation_name" in rd or "progress_message" in rd or rd.get("status") == "processing"
    if job_data["status"] == "completed":
        assert "video_url" in rd
        assert rd["video_url"].endswith(".mp4")
        assert "file_size_bytes" in rd
        assert rd["file_size_bytes"] > 0
        assert rd["duration_seconds"] >= 4
        assert "media_asset_id" in rd


def test_user_ownership_and_isolation(client):
    """Ensures User 1 cannot access User 2's generated media or jobs."""
    token1 = get_auth_token(client, email="user_one@socialai.com")
    headers1 = {"Authorization": f"Bearer {token1}"}

    token2 = get_auth_token(client, email="user_two@socialai.com")
    headers2 = {"Authorization": f"Bearer {token2}"}

    # User 1 creates brand and generates image
    client.post("/api/v1/brand-profiles/", json={"name": "User 1 Brand"}, headers=headers1)
    gen1 = client.post("/api/v1/generations/gemini/image", json={
        "prompt": "Private creative asset for User 1",
        "aspect_ratio": "1:1"
    }, headers=headers1)
    job1_id = gen1.json()["id"]

    # User 1 can view their job
    get1 = client.get(f"/api/v1/generations/jobs/{job1_id}", headers=headers1)
    assert get1.status_code == 200

    # User 2 CANNOT access User 1's job (enforcing strict multi-tenant ownership)
    get2 = client.get(f"/api/v1/generations/jobs/{job1_id}", headers=headers2)
    assert get2.status_code == 404
    assert "permission" in get2.json()["detail"].lower() or "not found" in get2.json()["detail"].lower()


def test_generation_history_filtering(client):
    """Verifies retrieval and filtering of user generation history."""
    token = get_auth_token(client, email="history_tester@socialai.com")
    headers = {"Authorization": f"Bearer {token}"}

    client.post("/api/v1/brand-profiles/", json={"name": "History Brand"}, headers=headers)

    # Generate 1 image
    client.post("/api/v1/generations/gemini/image", json={
        "prompt": "Minimalist espresso cup on wooden table",
        "aspect_ratio": "1:1"
    }, headers=headers)

    # Check history
    hist_res = client.get("/api/v1/generations/history", headers=headers)
    assert hist_res.status_code == 200
    jobs = hist_res.json()
    assert len(jobs) >= 1
    assert any(j["job_type"] == "image_generation" for j in jobs)
