import httpx
import json

BASE_URL = "http://127.0.0.1:8001/api/v1"

def test_api():
    client = httpx.Client(timeout=20.0)

    # 1. Health check
    res = client.get("http://127.0.0.1:8001/")
    assert res.status_code == 200, f"Health check failed: {res.text}"
    print("[OK] Health check passed:", res.json())

    # 2. Register user
    email = "admin@dollu.com"
    password = "AdminPass123!"
    reg_payload = {
        "email": email,
        "password": password,
        "full_name": "DOLLU Store Admin",
        "organization_name": "DOLLU Men's Fashion (Chennai)",
        "role": "creator"
    }
    res = client.post(f"{BASE_URL}/auth/register", json=reg_payload)
    if res.status_code == 201:
        print("[OK] Registration passed:", res.json()["email"])
    elif res.status_code == 400 and "already exists" in res.text:
        print("[OK] User already registered, proceeding to login.")
    else:
        print("[!] Register status:", res.status_code, res.text)

    # 3. Login
    login_payload = {"email": email, "password": password}
    res = client.post(f"{BASE_URL}/auth/login", json=login_payload)
    assert res.status_code == 200, f"Login failed: {res.text}"
    token_data = res.json()
    token = token_data["access_token"]
    print("[OK] Login passed, token received.")

    headers = {"Authorization": f"Bearer {token}"}

    # 4. Auth Me
    res = client.get(f"{BASE_URL}/auth/me", headers=headers)
    assert res.status_code == 200, f"Get me failed: {res.text}"
    user = res.json()
    print("[OK] Auth /me profile:", user["email"], f"Org ID: {user['organization_id']}")

    # 5. Brand Profiles
    res = client.get(f"{BASE_URL}/brand-profiles/", headers=headers)
    brands = res.json()
    if not brands:
        brand_payload = {
            "name": "DOLLU Men's Fashion",
            "industry": "Fashion & Apparel Retail",
            "tone_of_voice": "Modern, stylish and confident",
            "target_audience": "Men in Chennai aged 18-40",
            "brand_guidelines": "Elevate your style with breathable linen and sharp cuts.",
            "key_themes": ["Linen", "Suits", "Casual Denim"],
            "hashtags": ["#DOLLU", "#MensFashion", "#Chennai"]
        }
        res = client.post(f"{BASE_URL}/brand-profiles/", json=brand_payload, headers=headers)
        assert res.status_code == 201, f"Brand creation failed: {res.text}"
        brand = res.json()
    else:
        brand = brands[0]
    print("[OK] Brand profile verified:", brand["name"], f"ID: {brand['id']}")

    # 6. Create Content Post
    content_payload = {
        "title": "Summer Linen Drop 2026",
        "caption": "LEVEL UP YOUR STYLE WITH DOLLU. Breathable linen shirts for Chennai summers.",
        "hashtags": ["#DOLLU", "#MensFashion", "#Chennai"],
        "target_platforms": ["instagram"],
        "status": "draft",
        "brand_profile_id": brand["id"],
        "meta_info": {
            "headline": "LEVEL UP YOUR STYLE WITH DOLLU.",
            "format": "Image post",
            "contentType": "image",
            "cta": "Visit store in Chennai or DM for orders."
        }
    }
    res = client.post(f"{BASE_URL}/content/", json=content_payload, headers=headers)
    assert res.status_code == 201, f"Content creation failed: {res.text}"
    post = res.json()
    print("[OK] Content post created in MySQL:", post["title"], f"ID: {post['id']}")

    # 7. List Content
    res = client.get(f"{BASE_URL}/content/?brand_profile_id={brand['id']}", headers=headers)
    assert res.status_code == 200, f"List content failed: {res.text}"
    items = res.json()
    print(f"[OK] Content posts listed from MySQL: {len(items)} items found.")

    # 8. AI Text Generation
    gen_payload = {
        "prompt": "Summer breathable cotton shirts launch",
        "brand_profile_id": brand["id"],
        "tone_override": "Trendy & Sharp",
        "target_platforms": ["instagram"],
        "model_provider": "gemini"
    }
    res = client.post(f"{BASE_URL}/generations/text", json=gen_payload, headers=headers)
    assert res.status_code == 201, f"AI generation failed: {res.text}"
    gen_res = res.json()
    caption_sample = gen_res.get("result_data", {}).get("caption", "")[:80].encode('ascii', 'replace').decode('ascii')
    print("[OK] AI Generation Engine response:", caption_sample + "...")

    # 9. Campaign Create & List
    camp_payload = {
        "title": "DOLLU Festive Drop 2026",
        "objective": "Drive footfall to Chennai store",
        "brand_profile_id": brand["id"],
        "tags": ["Festive", "Suits"]
    }
    res = client.post(f"{BASE_URL}/campaigns/", json=camp_payload, headers=headers)
    assert res.status_code in [200, 201], f"Campaign creation failed: {res.text}"
    print("[OK] Campaign created in MySQL:", res.json()["title"])

    # 10. Analytics
    res = client.get(f"{BASE_URL}/analytics/overview/{brand['id']}", headers=headers)
    assert res.status_code == 200, f"Analytics failed: {res.text}"
    print("[OK] Analytics overview computed from database:", res.json())

    print("\n========================================================")
    print("ALL FASTAPI + MYSQL ENDPOINTS PASSED END-TO-END TESTING!")
    print("========================================================")

if __name__ == "__main__":
    test_api()
