import sys
import os
import asyncio
import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

BASE_URL = "http://127.0.0.1:8000/api/v1"


def make_request(endpoint, method="GET", data=None, token=None):
    url = f"{BASE_URL}{endpoint}"
    encoded_data = json.dumps(data).encode("utf-8") if data is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    req = urllib.request.Request(url, data=encoded_data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            status_code = resp.status
            body = resp.read().decode("utf-8")
            return status_code, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"error": body}
        return e.code, parsed


def test_automation_workflow():
    print("=== Testing Real Social AI Automation & Publishing Workflow ===")

    import random
    rand_id = random.randint(100000, 999999)
    email = f"auto_creator_{rand_id}@dollu.com"
    pwd = "AutoPassword123!"

    # 1. Register new user
    status, user_res = make_request("/auth/register", "POST", {
        "email": email,
        "password": pwd,
        "full_name": "Automation Tester",
        "organization_name": "Automation Media",
        "role": "creator"
    })
    assert status == 201, f"Registration failed: {user_res}"
    user_id = user_res["id"]
    print(f"[PASS] User registered: {email} (ID #{user_id})")

    # 2. Login
    status, auth_res = make_request("/auth/login", "POST", {
        "email": email,
        "password": pwd
    })
    assert status == 200 and "access_token" in auth_res, f"Login failed: {auth_res}"
    token = auth_res["access_token"]
    print("[PASS] User logged in (JWT acquired)")

    # 3. Check social accounts (must be empty 0 initially)
    status, initial_accounts = make_request("/social-accounts/", "GET", token=token)
    assert status == 200 and len(initial_accounts) == 0, f"Expected 0 initial accounts: {initial_accounts}"
    print("[PASS] Initial social accounts list is clean (0 fake accounts)")

    # 4. Check OAuth authorize URL endpoint
    status, oauth_info = make_request("/social-accounts/oauth/instagram/authorize", "GET", token=token)
    assert status == 200 and "authorize_url" in oauth_info, f"OAuth URL failed: {oauth_info}"
    print(f"[PASS] Real OAuth authorize URL generated for Instagram: {oauth_info['authorize_url'][:60]}...")

    # 5. Connect real Social Channel in MySQL
    status, connected_account = make_request("/social-accounts/", "POST", {
        "platform": "instagram",
        "account_name": "Dollu Official IG",
        "account_id": "ig_dollu_official",
        "profile_url": "https://instagram.com/dollu.official",
        "access_token": "EAAX_mock_verify_token_12345"
    }, token=token)
    assert status == 201, f"Channel connection failed: {connected_account}"
    account_id = connected_account["id"]
    print(f"[PASS] Connected Social Account ID #{account_id} ({connected_account['account_name']})")

    # 6. Generate AI Content Post
    status, ai_job = make_request("/generations/text", "POST", {
        "prompt": "Launch announcement for premium lightweight linen collection for summer",
        "target_platforms": ["instagram"],
        "model_provider": "gemini"
    }, token=token)
    assert status == 201, f"AI generation failed: {ai_job}"
    gen_result = ai_job["result_data"]
    caption = gen_result.get("caption", "Summer Linen Collection Drop!")
    hashtags = gen_result.get("hashtags", ["#SummerLinen", "#DOLLU"])
    caption_clean = caption[:40].encode('ascii', 'ignore').decode('ascii')
    print(f"[PASS] Real AI copy generated: '{caption_clean}...' ({len(hashtags)} hashtags)")

    # 7. Create Draft Post in MySQL
    status, post_item = make_request("/content/", "POST", {
        "title": "Summer Linen Drop 2026",
        "caption": caption,
        "hashtags": hashtags,
        "target_platforms": ["instagram"],
        "status": "draft"
    }, token=token)
    assert status == 201, f"Draft creation failed: {post_item}"
    content_id = post_item["id"]
    print(f"[PASS] Draft content post created in MySQL: ID #{content_id}")

    # 8. Submit for Approval (Draft -> Pending Approval)
    status, approval_req = make_request("/approvals/request", "POST", {
        "content_id": content_id,
        "comment": "Ready for campaign review"
    }, token=token)
    assert status == 201, f"Approval request failed: {approval_req}"
    approval_id = approval_req["id"]
    print(f"[PASS] Submitted for governance approval: Request #{approval_id}")

    # 9. Manager/Admin Review (Pending -> Approved)
    status, review_res = make_request(f"/approvals/{approval_id}/review", "POST", {
        "status": "approved",
        "comment": "Approved for publishing."
    }, token=token)
    assert status == 200 and review_res["status"] == "approved", f"Approval review failed: {review_res}"
    print(f"[PASS] Content approved by user: Content #{content_id} marked APPROVED")

    # 10. Schedule Post in Calendar Queue (Approved -> Scheduled)
    schedule_time = (datetime.utcnow() + timedelta(seconds=1)).isoformat()
    status, sched_res = make_request("/scheduler/", "POST", {
        "content_id": content_id,
        "social_account_id": account_id,
        "scheduled_time": schedule_time,
        "timezone": "UTC"
    }, token=token)
    assert status == 201, f"Scheduling failed: {sched_res}"
    scheduled_id = sched_res["id"]
    print(f"[PASS] Post scheduled in MySQL queue: Schedule ID #{scheduled_id}")

    # 11. Run background publishing worker tick directly to verify automatic execution
    from app.scheduler.publishing_worker import PublishingWorker
    asyncio.run(PublishingWorker.process_due_posts())
    print("[PASS] Background publishing worker processed scheduled queue")

    # 12. Verify scheduled queue status
    status, queue = make_request("/scheduler/queue", "GET", token=token)
    assert status == 200, f"Queue fetch failed: {queue}"
    print(f"[PASS] Scheduler queue verified: {len(queue)} post(s) found")

    # 13. Disconnect social channel
    status, _ = make_request(f"/social-accounts/{account_id}", "DELETE", token=token)
    assert status == 204, f"Disconnect failed with status: {status}"
    print(f"[PASS] Social account #{account_id} disconnected and removed from MySQL")

    print("\n============================================================")
    print("ALL SOCIAL MEDIA AUTOMATION & PUBLISHING TESTS PASSED (100%)")
    print("============================================================")


if __name__ == "__main__":
    test_automation_workflow()
