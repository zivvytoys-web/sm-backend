def get_auth_headers(client, email="contenttester@socialai.com"):
    client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "Password123!",
        "full_name": "Content Creator",
        "role": "creator"
    })
    res = client.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_create_and_update_content(client):
    headers = get_auth_headers(client)

    # First create a brand
    brand_res = client.post("/api/v1/brand-profiles/", json={
        "name": "Aura Cosmetics",
        "industry": "Beauty",
        "tone_of_voice": "Elegant"
    }, headers=headers)
    brand_id = brand_res.json()["id"]

    # Create Content Post
    content_payload = {
        "title": "Summer Glow Serum Launch",
        "caption": "Radiate confidence with our brand new botanical serum. ✨🌿",
        "hashtags": ["#SummerGlow", "#CleanBeauty"],
        "target_platforms": ["instagram", "facebook"],
        "brand_profile_id": brand_id
    }
    create_res = client.post("/api/v1/content/", json=content_payload, headers=headers)
    assert create_res.status_code == 201
    content_id = create_res.json()["id"]
    assert create_res.json()["title"] == "Summer Glow Serum Launch"

    # Update Content
    update_res = client.put(f"/api/v1/content/{content_id}", json={
        "caption": "Updated caption: Radiance meets clean botanical science! ✨"
    }, headers=headers)
    assert update_res.status_code == 200
    assert "clean botanical science" in update_res.json()["caption"]
