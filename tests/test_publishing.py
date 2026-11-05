from app.models.social_account import SocialAccount
from app.api.routes import publishing as publishing_route


def get_auth_token(client):
    email = "publishtester@socialai.com"
    client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "Password123!",
        "full_name": "Publisher Tester",
        "role": "creator"
    })
    res = client.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})
    return res.json()["access_token"]


def test_instant_publishing(client, db_session, monkeypatch):
    class MockLinkedInClient:
        def __init__(self, access_token):
            self.access_token = access_token

        async def publish_post(self, content_text, media_urls=None, **kwargs):
            return {
                "success": True,
                "status": "published",
                "platform": "linkedin",
                "platform_post_id": "mocked-linkedin-post",
                "permalink": "https://www.linkedin.com/feed/update/mock"
            }

    monkeypatch.setattr(publishing_route, "get_client_class", lambda _platform: MockLinkedInClient)
    token = get_auth_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    # Setup
    brand_res = client.post("/api/v1/brand-profiles/", json={"name": "Publish Brand"}, headers=headers)
    brand_id = brand_res.json()["id"]

    account = SocialAccount(
        brand_profile_id=brand_id,
        platform="linkedin",
        account_name="Publish Co",
        account_id="li_456",
        access_token="test-token",
        is_connected=True,
        meta_info={"verification_status": "VERIFIED"}
    )
    db_session.add(account)
    db_session.commit()
    acc_id = account.id

    content_res = client.post("/api/v1/content/", json={
        "title": "Breaking News",
        "caption": "Excited to share our newest innovation with the community today! 🚀",
        "brand_profile_id": brand_id
    }, headers=headers)
    content_id = content_res.json()["id"]

    # Publish instant
    pub_res = client.post(f"/api/v1/publishing/instant/{content_id}/{acc_id}", headers=headers)
    assert pub_res.status_code == 200
    assert pub_res.json()["status"] == "published", pub_res.json().get("errors")
    assert pub_res.json()["results"][0]["result"]["platform_post_id"] == "mocked-linkedin-post"
