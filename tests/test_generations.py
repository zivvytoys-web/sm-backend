def get_auth_token(client):
    
    email = "aigen@socialai.com"
    client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "Password123!",
        "full_name": "AI Designer",
        "role": "creator"
    })

    res = client.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})
    return res.json()["access_token"]


def test_ai_text_and_image_generation(client):
    token = get_auth_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    # Create Brand
    brand_res = client.post("/api/v1/brand-profiles/", json={"name": "GenAI Brand"}, headers=headers)
    brand_id = brand_res.json()["id"]

    # Test Text Gen
    gen_text_res = client.post("/api/v1/generations/text", json={
        "brand_profile_id": brand_id,
        "prompt": "Announce our revolutionary AI feature launch",
        "target_platforms": ["instagram", "linkedin"]
    }, headers=headers)
    assert gen_text_res.status_code == 201
    assert "result_data" in gen_text_res.json()
    assert "caption" in gen_text_res.json()["result_data"]

    # Test Image Gen
    gen_img_res = client.post("/api/v1/generations/image", json={
        "brand_profile_id": brand_id,
        "prompt": "Futuristic neon workspace with holograms",
        "aspect_ratio": "1:1"
    }, headers=headers)
    assert gen_img_res.status_code == 201
    assert "image_url" in gen_img_res.json()["result_data"]

