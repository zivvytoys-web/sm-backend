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


def test_admin_portal():
    print("=== Testing Admin Dashboard, User Management & Role Authorization ===")

    # 1. Login as Admin
    status, admin_auth = make_request("/auth/login", "POST", {
        "email": "admin@dollu.com",
        "password": "AdminPass123!"
    })
    assert status == 200 and "access_token" in admin_auth, f"Admin login failed: {admin_auth}"
    admin_token = admin_auth["access_token"]
    print("[PASS] Admin logged in successfully (JWT acquired)")

    # 2. Admin queries /admin/stats
    status, stats = make_request("/admin/stats", "GET", token=admin_token)
    assert status == 200 and "total_users" in stats, f"Admin stats failed: {stats}"
    print(f"[PASS] Admin stats retrieved from MySQL: {stats['total_users']} total users, {stats['active_users']} active")

    # 3. Admin queries /admin/users
    status, users_list = make_request("/admin/users", "GET", token=admin_token)
    assert status == 200 and isinstance(users_list, list), f"Admin users list failed: {users_list}"
    print(f"[PASS] Admin retrieved user management list: {len(users_list)} registered users")

    # 4. Admin queries /admin/analytics
    status, analytics = make_request("/admin/analytics", "GET", token=admin_token)
    assert status == 200 and "posts_by_status" in analytics, f"Admin analytics failed: {analytics}"
    print("[PASS] Admin platform analytics retrieved successfully")

    # 5. Admin queries /admin/settings
    status, settings = make_request("/admin/settings", "GET", token=admin_token)
    assert status == 200 and "active_ai_providers" in settings, f"Admin settings failed: {settings}"
    print("[PASS] Admin system settings retrieved successfully")

    # 6. Create a regular creator user
    import random
    rand_id = random.randint(100000, 999999)
    creator_email = f"creator_{rand_id}@dollu.com"
    creator_pwd = "CreatorPassword123!"

    status, creator_data = make_request("/auth/register", "POST", {
        "email": creator_email,
        "password": creator_pwd,
        "full_name": "Charlie Creator",
        "organization_name": "Creator Studio",
        "role": "creator"
    })
    assert status == 201, f"Creator registration failed: {creator_data}"
    creator_id = creator_data["id"]
    print(f"[PASS] Regular creator registered: {creator_email} (ID #{creator_id})")

    # 7. Creator logs in
    status, creator_auth = make_request("/auth/login", "POST", {
        "email": creator_email,
        "password": creator_pwd
    })
    assert status == 200 and "access_token" in creator_auth, f"Creator login failed: {creator_auth}"
    creator_token = creator_auth["access_token"]
    print("[PASS] Creator logged in successfully")

    # 8. Creator tries to access /admin/stats (MUST RETURN 403 FORBIDDEN)
    status, forbidden_stats = make_request("/admin/stats", "GET", token=creator_token)
    assert status == 403, f"Creator should be forbidden from admin stats, got: {status}"
    print(f"[PASS] Non-admin access to /admin/stats blocked (HTTP {status} Forbidden)")

    # 9. Creator tries to access /admin/users (MUST RETURN 403 FORBIDDEN)
    status, forbidden_users = make_request("/admin/users", "GET", token=creator_token)
    assert status == 403, f"Creator should be forbidden from admin users, got: {status}"
    print(f"[PASS] Non-admin access to /admin/users blocked (HTTP {status} Forbidden)")

    # 10. Admin deactivates the creator's account
    status, deactivated_res = make_request(f"/admin/users/{creator_id}/status", "PUT", {"is_active": False}, token=admin_token)
    assert status == 200 and deactivated_res["is_active"] is False, f"Deactivation failed: {deactivated_res}"
    print(f"[PASS] Admin successfully deactivated creator #{creator_id} in MySQL")

    # 11. Deactivated creator attempts to log in (MUST BE BLOCKED)
    status, blocked_login = make_request("/auth/login", "POST", {
        "email": creator_email,
        "password": creator_pwd
    })
    assert status == 400, f"Deactivated user login should be rejected with 400, got: {status} {blocked_login}"
    print(f"[PASS] Deactivated account login blocked: {blocked_login.get('detail')}")

    # 12. Admin reactivates creator's account
    status, reactivated_res = make_request(f"/admin/users/{creator_id}/status", "PUT", {"is_active": True}, token=admin_token)
    assert status == 200 and reactivated_res["is_active"] is True, f"Reactivation failed: {reactivated_res}"
    print(f"[PASS] Admin reactivated creator #{creator_id} in MySQL")

    # 13. Reactivated creator logs in successfully
    status, login_again = make_request("/auth/login", "POST", {
        "email": creator_email,
        "password": creator_pwd
    })
    assert status == 200, f"Reactivated creator login failed: {login_again}"
    print("[PASS] Reactivated creator logged in successfully")

    print("\n=======================================================")
    print("ALL ADMIN PORTAL & ROLE RESTRICTION TESTS PASSED (100%)")
    print("=======================================================")


if __name__ == "__main__":
    test_admin_portal()
