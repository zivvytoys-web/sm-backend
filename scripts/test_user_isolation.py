import json
import urllib.request
import urllib.error

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


def run_tests():
    print("=== Testing End-to-End Authentication, Ownership & Data Isolation ===")
    
    import random
    rand_id = random.randint(10000, 99999)
    email_a = f"user_a_{rand_id}@dollu.com"
    email_b = f"user_b_{rand_id}@dollu.com"
    pwd = "SecurePassword123!"

    # 1. Register User A
    status, user_a_data = make_request("/auth/register", "POST", {
        "email": email_a,
        "password": pwd,
        "full_name": "Alice Wonderland",
        "organization_name": "Alice Studio",
        "role": "creator"
    })
    assert status == 201, f"User A registration failed: {user_a_data}"
    print("[PASS] User A registered:", user_a_data["email"], "Org ID:", user_a_data["organization_id"])

    # 2. Login User A
    status, token_a_res = make_request("/auth/login", "POST", {
        "email": email_a,
        "password": pwd
    })
    assert status == 200 and "access_token" in token_a_res, f"User A login failed: {token_a_res}"
    token_a = token_a_res["access_token"]
    print("[PASS] User A logged in successfully (JWT acquired)")

    # 3. Register User B
    status, user_b_data = make_request("/auth/register", "POST", {
        "email": email_b,
        "password": pwd,
        "full_name": "Bob Builder",
        "organization_name": "Bob Enterprises",
        "role": "creator"
    })
    assert status == 201, f"User B registration failed: {user_b_data}"
    print("[PASS] User B registered:", user_b_data["email"], "Org ID:", user_b_data["organization_id"])

    # 4. Login User B
    status, token_b_res = make_request("/auth/login", "POST", {
        "email": email_b,
        "password": pwd
    })
    assert status == 200 and "access_token" in token_b_res, f"User B login failed: {token_b_res}"
    token_b = token_b_res["access_token"]
    print("[PASS] User B logged in successfully (JWT acquired)")

    # 5. User A gets brand profiles
    status, brands_a = make_request("/brand-profiles/", "GET", token=token_a)
    assert status == 200 and len(brands_a) >= 1, f"User A should have default brand profile: {brands_a}"
    brand_a_id = brands_a[0]["id"]
    print(f"[PASS] User A brand profile found: ID {brand_a_id} ({brands_a[0]['name']})")

    # 6. User A creates content post
    status, post_a = make_request("/content/", "POST", {
        "title": "Alice Special Collection",
        "caption": "Check out our brand new exclusive drop! #Fashion",
        "hashtags": ["#Fashion", "#AliceStyle"],
        "target_platforms": ["instagram"],
        "status": "draft",
        "brand_profile_id": brand_a_id
    }, token=token_a)
    assert status == 201, f"User A post creation failed: {post_a}"
    post_a_id = post_a["id"]
    print(f"[PASS] User A created content post ID {post_a_id}")

    # 7. User A lists content (should contain 1 item)
    status, list_a = make_request("/content/", "GET", token=token_a)
    assert status == 200 and len(list_a) == 1, f"User A content list should have 1 item: {list_a}"
    print(f"[PASS] User A content list verified: {len(list_a)} post(s)")

    # 8. User B lists content (SHOULD BE EMPTY - 0 items)
    status, list_b = make_request("/content/", "GET", token=token_b)
    assert status == 200 and len(list_b) == 0, f"User B should see 0 posts, got: {list_b}"
    print("[PASS] DATA ISOLATION PASSED: User B sees 0 posts (isolated workspace)")

    # 9. User B tries to access User A's post directly (MUST BE BLOCKED / 404)
    status, cross_get = make_request(f"/content/{post_a_id}", "GET", token=token_b)
    assert status in (404, 403), f"User B should not be able to get User A's post: {status} {cross_get}"
    print(f"[PASS] AUTHORIZATION PASSED: User B blocked from accessing User A post (HTTP {status})")

    # 10. User B tries to delete User A's post directly (MUST BE BLOCKED / 404)
    status, cross_del = make_request(f"/content/{post_a_id}", "DELETE", token=token_b)
    assert status in (404, 403), f"User B should not be able to delete User A's post: {status} {cross_del}"
    print(f"[PASS] AUTHORIZATION PASSED: User B blocked from deleting User A post (HTTP {status})")

    # 11. User A updates profile
    status, update_res = make_request(f"/users/{user_a_data['id']}", "PUT", {
        "full_name": "Alice Updated",
        "avatar_url": "https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=150"
    }, token=token_a)
    assert status == 200 and update_res["full_name"] == "Alice Updated", f"Profile update failed: {update_res}"
    print("[PASS] User A profile updated in MySQL database")

    # 12. User A fetches /auth/me to verify profile persistence
    status, me_a = make_request("/auth/me", "GET", token=token_a)
    assert status == 200 and me_a["full_name"] == "Alice Updated", f"/auth/me check failed: {me_a}"
    print(f"[PASS] User A /auth/me verified: {me_a['full_name']} (Avatar: {me_a['avatar_url']})")

    # 13. Invalid/missing token request (MUST BE 401)
    status, unauth_res = make_request("/auth/me", "GET", token="invalid-token-12345")
    assert status == 401, f"Unauthenticated request should return 401, got: {status}"
    print("[PASS] SECURITY PASSED: Invalid token returns 401 Unauthorized")

    print("\nALL 13 VERIFICATION TESTS PASSED SUCCESSFULLY! 100% SUCCESS.")


if __name__ == "__main__":
    run_tests()
