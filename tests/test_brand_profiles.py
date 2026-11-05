def get_auth_token(client):
    email = "brandtester@socialai.com"
    client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "Password123!",
        "full_name": "Brand Tester",
        "role": "admin"
    })
    res = client.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})
    return res.json()["access_token"]


def test_create_and_list_brand_profile(client):
    token = get_auth_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    # Create Brand
    brand_payload = {
        "name": "Nova Cyber",
        "industry": "Gaming & Web3",
        "tone_of_voice": "Bold, rebellious, innovative",
        "key_themes": ["NextGen Hardware", "Esports"],
        "hashtags": ["#NovaGaming", "#PlayFuture"],
        "color_palette": ["#111827", "#8B5CF6"]
    }
    create_res = client.post("/api/v1/brand-profiles/", json=brand_payload, headers=headers)
    assert create_res.status_code == 201
    brand_data = create_res.json()
    assert brand_data["name"] == "Nova Cyber"
    brand_id = brand_data["id"]

    # List Brands
    list_res = client.get("/api/v1/brand-profiles/", headers=headers)
    assert list_res.status_code == 200
    assert len(list_res.json()) >= 1
