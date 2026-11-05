import uuid
import pytest
from app.services.auth_service import AuthService


def test_health_check(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_register_and_login(client):
    unique_email = f"user_{uuid.uuid4().hex[:8]}@socialai.com"
    # 1. Register
    reg_payload = {
        "email": unique_email,
        "password": "SecretPassword123!",
        "full_name": "Test User",
        "organization_name": "Acme Brand Studio",
        "role": "creator"
    }
    reg_res = client.post("/api/v1/auth/register", json=reg_payload)
    assert reg_res.status_code == 201
    user_data = reg_res.json()
    assert user_data["email"] == unique_email

    # 2. Login
    login_payload = {
        "email": unique_email,
        "password": "SecretPassword123!"
    }
    login_res = client.post("/api/v1/auth/login", json=login_payload)
    assert login_res.status_code == 200
    token_data = login_res.json()
    assert "access_token" in token_data

    # Verify HTTP-only cookie was set
    assert "access_token" in login_res.cookies

    # 3. Get profile with Bearer token
    headers = {"Authorization": f"Bearer {token_data['access_token']}"}
    me_res = client.get("/api/v1/auth/me", headers=headers)
    assert me_res.status_code == 200
    assert me_res.json()["email"] == unique_email

    # 4. Get profile using HTTP-only cookie (without Bearer header)
    client.cookies.set("access_token", token_data["access_token"])
    me_cookie_res = client.get("/api/v1/auth/me")
    assert me_cookie_res.status_code == 200
    assert me_cookie_res.json()["email"] == unique_email

    # 5. Logout clears cookies
    logout_res = client.post("/api/v1/auth/logout")
    assert logout_res.status_code == 200
    set_cookie_hdr = logout_res.headers.get("set-cookie", "")
    assert "access_token" in set_cookie_hdr and ('max-age=0' in set_cookie_hdr.lower() or 'access_token=""' in set_cookie_hdr or 'access_token=;' in set_cookie_hdr)


def test_password_reset_flow(client):
    unique_email = f"reset_{uuid.uuid4().hex[:8]}@socialai.com"
    old_pw = "OldPassword123!"
    new_pw = "BrandNewSecretPassword456!"

    # 1. Create account
    client.post("/api/v1/auth/register", json={
        "email": unique_email,
        "password": old_pw,
        "full_name": "Reset User",
        "organization_name": "Reset Workspace"
    })

    # 2. Request password reset
    req_res = client.post("/api/v1/auth/password-reset/request", json={"email": unique_email})
    assert req_res.status_code == 200
    res_data = req_res.json()
    reset_token = res_data.get("reset_token")
    assert reset_token is not None

    # 3. Confirm password reset
    confirm_res = client.post("/api/v1/auth/password-reset/confirm", json={
        "token": reset_token,
        "new_password": new_pw
    })
    assert confirm_res.status_code == 200

    # 4. Old password should fail
    fail_res = client.post("/api/v1/auth/login", json={"email": unique_email, "password": old_pw})
    assert fail_res.status_code == 401

    # 5. New password should succeed
    success_res = client.post("/api/v1/auth/login", json={"email": unique_email, "password": new_pw})
    assert success_res.status_code == 200
    assert "access_token" in success_res.json()


def test_email_verification_flow(client):
    unique_email = f"verify_{uuid.uuid4().hex[:8]}@socialai.com"

    # 1. Create account
    client.post("/api/v1/auth/register", json={
        "email": unique_email,
        "password": "Password123!",
        "full_name": "Verify User",
        "organization_name": "Verify Workspace"
    })

    # 2. Request email verification
    req_res = client.post("/api/v1/auth/verify-email/request", json={"email": unique_email})
    assert req_res.status_code == 200
    v_token = req_res.json().get("verification_token")
    assert v_token is not None

    # 3. Confirm email verification
    confirm_res = client.post("/api/v1/auth/verify-email/confirm", json={"token": v_token})
    assert confirm_res.status_code == 200
    assert "verified" in confirm_res.json()["message"].lower()


def test_login_rate_limiting(client):
    bad_email = f"brute_{uuid.uuid4().hex[:8]}@socialai.com"

    # Make 5 failed attempts
    for _ in range(5):
        res = client.post("/api/v1/auth/login", json={"email": bad_email, "password": "WrongPassword!"})
        assert res.status_code in [401, 429]

    # 6th attempt should be blocked with 429 Too Many Requests
    blocked_res = client.post("/api/v1/auth/login", json={"email": bad_email, "password": "WrongPassword!"})
    assert blocked_res.status_code == 429
    assert "too many" in blocked_res.json()["detail"].lower()

