import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.config import settings


@pytest.fixture
def client():
    return TestClient(app)


def test_meta_webhook_verification_success(client: TestClient):
    token = settings.META_WEBHOOK_VERIFY_TOKEN
    challenge = "meta_challenge_123456789"
    
    # Test all aliases
    for endpoint in ["/api/webhooks/meta", "/api/webhooks/facebook", "/api/webhooks/instagram", "/api/v1/social-accounts/webhooks/meta"]:
        res = client.get(
            f"{endpoint}?hub.mode=subscribe&hub.verify_token={token}&hub.challenge={challenge}"
        )
        assert res.status_code == 200
        assert res.text == challenge


def test_meta_webhook_verification_forbidden_invalid_token(client: TestClient):
    challenge = "meta_challenge_123456789"
    res = client.get(
        f"/api/webhooks/meta?hub.mode=subscribe&hub.verify_token=wrong_token&hub.challenge={challenge}"
    )
    assert res.status_code == 403


def test_meta_webhook_event_post(client: TestClient):
    payload = {
        "object": "instagram",
        "entry": [{"id": "1784140001", "time": 1700000000, "changes": []}]
    }
    res = client.post("/api/webhooks/instagram", json=payload)
    assert res.status_code == 200
    assert res.json()["status"] == "EVENT_RECEIVED"


def test_youtube_webhook_verification(client: TestClient):
    res = client.get("/api/webhooks/youtube?hub.challenge=yt_chal_999")
    assert res.status_code == 200
    assert res.text == "yt_chal_999"


def test_twitter_crc_verification(client: TestClient):
    res = client.get("/api/webhooks/twitter?crc_token=sample_crc_token_123")
    assert res.status_code == 200
    data = res.json()
    assert "response_token" in data
    assert data["response_token"].startswith("sha256=")


def test_tiktok_and_linkedin_webhooks(client: TestClient):
    res_tt = client.get("/api/webhooks/tiktok?challenge=tt_chal_555")
    assert res_tt.status_code == 200
    assert res_tt.text == "tt_chal_555"

    res_li = client.get("/api/webhooks/linkedin?challengeCode=li_code_888")
    assert res_li.status_code == 200
    assert res_li.text == "li_code_888"
