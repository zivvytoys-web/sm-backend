with open('app/api/routes/generations.py', 'r', encoding='utf-8') as f:
    content = f.read()

target = """    image_data = dict(image_job.result_data or {})

    # Register MediaAsset for Image
    media_asset = MediaAsset(
        file_name=image_data.get("file_name", "creative.png"),
        file_url=image_data.get("image_url", ""),
        file_size_bytes=image_data.get("file_size_bytes", 0),
        mime_type="image/png",
        media_type=MediaType.IMAGE,
        meta_info={
            "prompt": image_prompt,
            "aspect_ratio": payload.aspect_ratio,
            "brand_name": brand_name,
            "provider": "google-gemini-visuals"
        },
        organization_id=current_user.organization_id,
        user_id=current_user.id
    )
    db.add(media_asset)
    db.flush()
    image_data["media_asset_id"] = media_asset.id"""

replacement = """    image_data = dict(image_job.result_data or {})
    if image_job and image_job.result_data and image_job.result_data.get("media_asset_id"):
        image_data["media_asset_id"] = image_job.result_data["media_asset_id"]
        image_data["quality_check"] = image_job.result_data.get("quality_check")

    # Validate Bundle Text Description
    from app.services.quality_check_engine import QualityCheckEngine
    text_val = QualityCheckEngine.validate_description(
        caption=text_data.get("caption") or text_data.get("headline") or text_prompt,
        title=text_data.get("headline"),
        hashtags=text_data.get("hashtags", []),
        brand=brand,
        target_platforms=[target_platform_str],
        organization_id=current_user.organization_id,
        db=db
    )
    text_data["quality_check"] = text_val"""

# Normalize line endings
content_norm = content.replace("\r\n", "\n")
target_norm = target.replace("\r\n", "\n")
replacement_norm = replacement.replace("\r\n", "\n")

if target_norm in content_norm:
    updated = content_norm.replace(target_norm, replacement_norm, 1)
    with open('app/api/routes/generations.py', 'w', encoding='utf-8') as f:
        f.write(updated)
    print("Bundle generation successfully updated with QualityCheckEngine!")
else:
    print("Could not match target block.")
