from datetime import datetime, timedelta
from app.models.social_account import SocialAccount


def get_auth_token(client):
    email = "schedulertester@socialai.com"
    client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "Password123!",
        "full_name": "Schedule Master",
        "role": "creator"
    })
    res = client.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})
    return res.json()["access_token"]


def test_schedule_post(client, db_session):
    token = get_auth_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    # Setup brand, social account, and content
    brand_res = client.post("/api/v1/brand-profiles/", json={"name": "Schedule Brand"}, headers=headers)
    brand_id = brand_res.json()["id"]

    account = SocialAccount(
        brand_profile_id=brand_id,
        platform="instagram",
        account_name="@schedbrand",
        account_id="ig_123",
        is_connected=True,
        meta_info={"verification_status": "VERIFIED"}
    )
    db_session.add(account)
    db_session.commit()
    acc_id = account.id

    content_res = client.post("/api/v1/content/", json={
        "title": "Scheduled announcement",
        "caption": "Scheduled for tomorrow afternoon!",
        "brand_profile_id": brand_id
    }, headers=headers)
    content_id = content_res.json()["id"]

    # Schedule post
    scheduled_time = (datetime.utcnow() + timedelta(days=1)).isoformat()
    sched_res = client.post("/api/v1/scheduler/", json={
        "content_id": content_id,
        "social_account_id": acc_id,
        "scheduled_time": scheduled_time,
        "timezone": "UTC"
    }, headers=headers)

    assert sched_res.status_code == 201
    assert sched_res.json()["status"] == "scheduled"
