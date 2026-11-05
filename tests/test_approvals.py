def test_approval_flow(client):
    # 1. Register manager who creates the organization
    mgr_reg_res = client.post("/api/v1/auth/register", json={
        "email": "manager_app@socialai.com",
        "password": "Password123!",
        "full_name": "Manager User",
        "role": "manager",
        "organization_name": "Approval Test Org"
    })
    assert mgr_reg_res.status_code == 201
    mgr_user = mgr_reg_res.json()
    org_id = mgr_user["organization_id"]

    mgr_login_res = client.post("/api/v1/auth/login", json={"email": "manager_app@socialai.com", "password": "Password123!"})
    manager_token = mgr_login_res.json()["access_token"]
    manager_headers = {"Authorization": f"Bearer {manager_token}"}

    # 2. Register creator in the same organization via business_id
    creator_reg_res = client.post("/api/v1/auth/register", json={
        "email": "creator_app@socialai.com",
        "password": "Password123!",
        "full_name": "Creator User",
        "role": "creator",
        "business_id": org_id
    })
    assert creator_reg_res.status_code == 201

    creator_login_res = client.post("/api/v1/auth/login", json={"email": "creator_app@socialai.com", "password": "Password123!"})
    creator_token = creator_login_res.json()["access_token"]
    creator_headers = {"Authorization": f"Bearer {creator_token}"}

    # 3. Create brand under this organization
    brand_res = client.post("/api/v1/brand-profiles/", json={"name": "Approval Brand"}, headers=manager_headers)
    assert brand_res.status_code == 201
    brand_id = brand_res.json()["id"]

    # 4. Creator creates content draft under the brand
    content_res = client.post("/api/v1/content/", json={
        "title": "Post for review",
        "caption": "Please review this post draft.",
        "brand_profile_id": brand_id
    }, headers=creator_headers)
    assert content_res.status_code == 201
    content_id = content_res.json()["id"]

    # 5. Request approval
    approval_res = client.post("/api/v1/approvals/request", json={
        "content_id": content_id,
        "comment": "Ready for campaign launch review"
    }, headers=creator_headers)
    assert approval_res.status_code == 201
    approval_id = approval_res.json()["id"]

    # 6. Review approval by manager
    review_res = client.post(f"/api/v1/approvals/{approval_id}/review", json={
        "status": "approved",
        "comment": "Looks great, approved for posting!"
    }, headers=manager_headers)
    assert review_res.status_code == 200
    assert review_res.json()["status"] == "approved"
