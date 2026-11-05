import os
from typing import Dict, Any, Optional
import httpx
from app.core.config import settings
from app.core.logging import logger
from app.services.gemini_service import GeminiService


class AIImageProvider:
    """Provider for generating AI visual assets using Google Imagen 3."""

    @classmethod
    async def generate_image(
        cls,
        prompt: str,
        aspect_ratio: str = "1:1",
        style: str = "modern_product",
        provider: str = "imagen-3",
        negative_prompt: str = "blurry, distorted, low quality, artifacts",
        model_name: str = "imagen-3.0-generate-002",
        organization_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Generate AI image via Google Imagen 3, OpenAI DALL-E, or Stability AI."""
        
        # 1. Google Imagen 3 (Default & Recommended)
        if settings.GEMINI_API_KEY and (provider == "imagen-3" or provider == "gemini" or not settings.OPENAI_API_KEY):
            try:
                result = await GeminiService.generate_image(
                    prompt=prompt,
                    aspect_ratio=aspect_ratio,
                    style_preset=style,
                    negative_prompt=negative_prompt,
                    model_name=model_name or "imagen-3.0-generate-002",
                    organization_id=organization_id
                )
                return result
            except Exception as e:
                logger.error(f"Google Imagen 3 generation failed: {str(e)}")

        # 2. OpenAI DALL-E 3
        if settings.OPENAI_API_KEY and (provider == "openai" or provider == "dall-e"):
            try:
                size_map = {
                    "1:1": "1024x1024",
                    "9:16": "1024x1792",
                    "16:9": "1792x1024"
                }
                dalle_size = size_map.get(aspect_ratio, "1024x1024")
                async with httpx.AsyncClient(timeout=45.0) as client:
                    response = await client.post(
                        "https://api.openai.com/v1/images/generations",
                        headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                        json={
                            "model": "dall-e-3",
                            "prompt": f"{prompt}, {style}",
                            "n": 1,
                            "size": dalle_size
                        }
                    )
                    if response.status_code == 200:
                        data = response.json()
                        return {
                            "image_url": data["data"][0]["url"],
                            "model_name": "dall-e-3",
                            "aspect_ratio": aspect_ratio,
                            "prompt": prompt,
                            "revised_prompt": data["data"][0].get("revised_prompt", prompt)
                        }
            except Exception as e:
                logger.error(f"DALL-E generation failed: {str(e)}")
                raise ValueError(f"DALL-E generation failed: {str(e)}")

        raise ValueError(
            "Image generation unavailable: GEMINI_API_KEY is not configured or failed to generate the image. "
            "Please ensure your GEMINI_API_KEY has image generation quota or billing enabled on Google AI Studio."
        )
