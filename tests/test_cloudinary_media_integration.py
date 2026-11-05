import io
from unittest.mock import patch
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from app.main import app
from app.db.session import SessionLocal
from app.models.user import User
from app.models.organization import Organization
from app.models.media_asset import MediaAsset
from app.core.security import get_password_hash
from app.utils.enums import UserRole, MediaType

client = TestClient(app)


def create_valid_png_bytes(width=80, height=80, color="blue") -> bytes:
    """Creates a real in-memory PNG image."""
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def setup_test_users_and_orgs():
    db = SessionLocal()
    
    # 1. Clean up existing test entities
    test_emails = ["media_user_a@test.com", "media_user_b@test.com"]
    for email in test_emails:
        u = db.query(User).filter(User.email == email).first()
        if u:
            db.query(MediaAsset).filter(MediaAsset.user_id == u.id).delete()
            db.delete(u)
    
    org_a = db.query(Organization).filter(Organization.slug == "media-org-alpha").first()
    if org_a:
        db.query(MediaAsset).filter(MediaAsset.organization_id == org_a.id).delete()
        db.delete(org_a)
        
    org_b = db.query(Organization).filter(Organization.slug == "media-org-beta").first()
    if org_b:
        db.query(MediaAsset).filter(MediaAsset.organization_id == org_b.id).delete()
        db.delete(org_b)
    
    db.commit()

    # 2. Create Org Alpha and Org Beta
    org_a = Organization(
        name="Media Test Org Alpha",
        slug="media-org-alpha",
        settings={"category": "Fashion"}
    )
    db.add(org_a)
    
    org_b = Organization(
        name="Media Test Org Beta",
        slug="media-org-beta",
        settings={"category": "Watches"}
    )
    db.add(org_b)
    db.commit()
    db.refresh(org_a)
    db.refresh(org_b)

    # 3. Create User A (Org Alpha) and User B (Org Beta)
    user_a = User(
        email="media_user_a@test.com",
        hashed_password=get_password_hash("Password123!"),
        full_name="User Alpha Creator",
        role=UserRole.CREATOR,
        organization_id=org_a.id,
        is_active=True
    )
    db.add(user_a)

    user_b = User(
        email="media_user_b@test.com",
        hashed_password=get_password_hash("Password123!"),
        full_name="User Beta Creator",
        role=UserRole.CREATOR,
        organization_id=org_b.id,
        is_active=True
    )
    db.add(user_b)
    db.commit()
    db.refresh(user_a)
    db.refresh(user_b)

    yield {
        "org_a_id": org_a.id,
        "org_b_id": org_b.id,
        "user_a_id": user_a.id,
        "user_b_id": user_b.id,
    }

    # Cleanup after tests
    db.query(MediaAsset).filter(MediaAsset.organization_id.in_([org_a.id, org_b.id])).delete()
    u_a = db.query(User).filter(User.email == "media_user_a@test.com").first()
    if u_a:
        db.delete(u_a)
    u_b = db.query(User).filter(User.email == "media_user_b@test.com").first()
    if u_b:
        db.delete(u_b)
    db.query(Organization).filter(Organization.id.in_([org_a.id, org_b.id])).delete()
    db.commit()
    db.close()


def get_auth_token(email: str, password: str = "Password123!") -> str:
    res = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, f"Login failed: {res.text}"
    return res.json()["access_token"]


def test_cloudinary_upload_image_success(setup_test_users_and_orgs):
    """Test successful image upload with metadata, public_id, and user ownership in DB."""
    token_a = get_auth_token("media_user_a@test.com")
    headers = {"Authorization": f"Bearer {token_a}"}

    png_bytes = create_valid_png_bytes(width=120, height=120, color="purple")
    files = {"file": ("banner_spring.png", io.BytesIO(png_bytes), "image/png")}

    res = client.post("/api/v1/media/upload", headers=headers, files=files)
    assert res.status_code == 201, f"Image upload failed: {res.text}"

    data = res.json()
    assert data["file_name"] == "banner_spring.png"
    assert data["media_type"].lower() == "image"
    assert "public_id" in data and data["public_id"]
    assert "file_url" in data and data["file_url"]
    assert data["organization_id"] == setup_test_users_and_orgs["org_a_id"]
    assert data["user_id"] == setup_test_users_and_orgs["user_a_id"]

    # Verify directly in MySQL
    db = SessionLocal()
    saved = db.query(MediaAsset).filter(MediaAsset.id == data["id"]).first()
    assert saved is not None
    assert saved.file_name == "banner_spring.png"
    assert saved.organization_id == setup_test_users_and_orgs["org_a_id"]
    assert saved.user_id == setup_test_users_and_orgs["user_a_id"]
    assert saved.public_id == data["public_id"]
    db.close()


def test_cloudinary_upload_video_success(setup_test_users_and_orgs):
    """Test successful video upload (MP4) with video media_type and metadata."""
    token_a = get_auth_token("media_user_a@test.com")
    headers = {"Authorization": f"Bearer {token_a}"}

    # Mock cloudinary uploader return for video to avoid network video transcoding overhead
    mock_video_cloudinary_res = {
        "secure_url": "https://res.cloudinary.com/e2yafttz/video/upload/v1791783000/socialai/org_22/promo_reel.mp4",
        "public_id": "socialai/org_22/promo_reel",
        "bytes": 2048,
        "format": "mp4",
        "resource_type": "video",
        "width": 1080,
        "height": 1920,
        "duration": 14.5,
        "created_at": "2026-10-12T10:00:00Z",
        "etag": "mock_etag_video_123"
    }

    fake_mp4_bytes = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 500
    files = {"file": ("promo_reel.mp4", io.BytesIO(fake_mp4_bytes), "video/mp4")}

    with patch("cloudinary.uploader.upload", return_value=mock_video_cloudinary_res):
        res = client.post("/api/v1/media/upload", headers=headers, files=files)
        assert res.status_code == 201, f"Video upload failed: {res.text}"

        data = res.json()
        assert data["file_name"] == "promo_reel.mp4"
        assert data["media_type"].lower() == "video"
        assert data["public_id"] == "socialai/org_22/promo_reel"
        assert data["meta_info"]["resource_type"] == "video"
        assert data["meta_info"]["duration"] == 14.5


def test_cloudinary_invalid_file_format_rejected(setup_test_users_and_orgs):
    """Test validation reject of unsupported file formats (e.g. .exe, .pdf)."""
    token_a = get_auth_token("media_user_a@test.com")
    headers = {"Authorization": f"Bearer {token_a}"}

    # Unsupported format
    fake_exe = b"MZ\x90\x00" + b"\x00" * 100
    files = {"file": ("malicious_payload.exe", io.BytesIO(fake_exe), "application/x-msdownload")}

    res = client.post("/api/v1/media/upload", headers=headers, files=files)
    assert res.status_code == 400
    assert "Unsupported file format" in res.json()["detail"]


def test_cloudinary_file_size_limit_rejected(setup_test_users_and_orgs):
    """Test rejection when file exceeds size limit (Max 10MB for image)."""
    token_a = get_auth_token("media_user_a@test.com")
    headers = {"Authorization": f"Bearer {token_a}"}

    # 11 MB fake PNG (exceeds 10 MB limit)
    oversized_bytes = b"\x89PNG\r\n\x1a\n" + (b"A" * (11 * 1024 * 1024))
    files = {"file": ("massive_poster.png", io.BytesIO(oversized_bytes), "image/png")}

    res = client.post("/api/v1/media/upload", headers=headers, files=files)
    assert res.status_code == 400
    assert "exceeds" in res.json()["detail"]


def test_cloudinary_signature_generation(setup_test_users_and_orgs):
    """Test backend generation of secure upload signature without exposing API Secret."""
    token_a = get_auth_token("media_user_a@test.com")
    headers = {"Authorization": f"Bearer {token_a}"}

    res = client.get("/api/v1/media/signature", headers=headers)
    assert res.status_code == 200

    data = res.json()
    assert "timestamp" in data
    assert "signature" in data
    assert "api_key" in data
    assert "cloud_name" in data
    assert "folder" in data
    # Guarantee that secret is NEVER returned
    assert "api_secret" not in data
    assert "CLOUDINARY_API_SECRET" not in data


def test_media_data_isolation_between_organizations(setup_test_users_and_orgs):
    """Test that User B in Org Beta cannot see or delete User A's media in Org Alpha."""
    token_a = get_auth_token("media_user_a@test.com")
    token_b = get_auth_token("media_user_b@test.com")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # 1. User A uploads a real image
    png_bytes = create_valid_png_bytes(width=100, height=100, color="orange")
    res_upload = client.post(
        "/api/v1/media/upload",
        headers=headers_a,
        files={"file": ("alpha_exclusive.png", io.BytesIO(png_bytes), "image/png")}
    )
    assert res_upload.status_code == 201
    asset_a_id = res_upload.json()["id"]

    # 2. User B lists media -> MUST NOT see asset_a_id
    res_list_b = client.get("/api/v1/media/", headers=headers_b)
    assert res_list_b.status_code == 200
    b_assets = res_list_b.json()
    assert not any(item["id"] == asset_a_id for item in b_assets)

    # 3. User B tries to inspect User A's asset directly -> 404 access denied
    res_get_unauth = client.get(f"/api/v1/media/{asset_a_id}", headers=headers_b)
    assert res_get_unauth.status_code == 404

    # 4. User B tries to delete User A's asset -> 403 Forbidden
    res_del_unauth = client.delete(f"/api/v1/media/{asset_a_id}", headers=headers_b)
    assert res_del_unauth.status_code == 403

    # 5. User A can successfully delete their own asset
    res_del_auth = client.delete(f"/api/v1/media/{asset_a_id}", headers=headers_a)
    assert res_del_auth.status_code == 200

    # 6. Verify deleted from DB
    db = SessionLocal()
    assert db.query(MediaAsset).filter(MediaAsset.id == asset_a_id).first() is None
    db.close()
