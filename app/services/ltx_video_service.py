"""
Dedicated client for Lightricks LTX Video Generation API (api.ltx.video / api.ltx.io).
Provides fast, broadcast-quality AI video generation with synchronized Edge Neural TTS voiceover.
Billed at $0.09/s on ltx-2-5-fast (720p). A 5s neural hero cut extended to a 30s commercial reel costs ~$0.45 (₹38.70).
"""

import os
import uuid
import asyncio
import subprocess
import shutil
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import httpx
import imageio_ffmpeg

from app.core.config import settings
from app.core.logging import logger
from app.models.brand_profile import BrandProfile
from app.integrations.storage.cloudinary_storage import CloudinaryStorageProvider
from app.services.voice_service import VoiceService


class LTXVideoService:
    """Enterprise client for Lightricks LTX Video Generation."""

    BASE_URL = "https://api.ltx.video"
    AI_VIDEOS_DIR = os.path.join(settings.LOCAL_STORAGE_DIR, "media", "ai_videos")

    # Estimated pricing rates
    RATE_PER_SECOND_USD = 0.09  # ltx-2-5-fast @ 720p
    USD_TO_INR_RATE = 86.0

    @classmethod
    def get_api_key(cls) -> Optional[str]:
        return getattr(settings, "LTX_API_KEY", None) or os.getenv("LTX_API_KEY")

    @classmethod
    def is_configured(cls) -> bool:
        key = cls.get_api_key()
        return bool(key and key.strip().startswith("ltxv_"))

    @classmethod
    def _ensure_directories(cls):
        os.makedirs(cls.AI_VIDEOS_DIR, exist_ok=True)

    @classmethod
    def _get_headers(cls) -> Dict[str, str]:
        key = cls.get_api_key()
        if not key:
            raise ValueError("LTX_API_KEY is not configured in backend settings.")
        return {
            "Authorization": f"Bearer {key.strip()}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    @classmethod
    def calculate_cost(cls, clip_duration_seconds: int) -> Dict[str, float]:
        """Calculates exact generation cost for logging and budget controls."""
        cost_usd = round(clip_duration_seconds * cls.RATE_PER_SECOND_USD, 4)
        cost_inr = round(cost_usd * cls.USD_TO_INR_RATE, 2)
        return {"cost_usd": cost_usd, "cost_inr": cost_inr}

    @classmethod
    async def create_text_to_video_job(
        cls,
        prompt: str,
        duration_seconds: int = 5,
        resolution: str = "1280x720",
        model: str = "ltx-2-5-fast"
    ) -> str:
        """
        Submits an asynchronous text-to-video generation job to LTX Video API.
        Returns the job ID.
        """
        headers = cls._get_headers()
        # Clip duration for initial generation: 5 to 8 seconds to remain within budget
        safe_duration = max(4, min(int(duration_seconds), 8))
        
        cost_info = cls.calculate_cost(safe_duration)
        logger.info(
            f"[LTXVideo] Initiating generation job: model='{model}', duration={safe_duration}s, "
            f"est_cost=${cost_info['cost_usd']} (₹{cost_info['cost_inr']}). Prompt: '{prompt[:60]}...'"
        )

        payload = {
            "prompt": prompt[:4000],
            "model": model,
            "duration": safe_duration,
            "resolution": resolution,
            "generate_audio": False
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.post(f"{cls.BASE_URL}/v2/text-to-video", json=payload, headers=headers)
            if res.status_code not in (200, 202):
                raise RuntimeError(f"LTX Video job creation failed ({res.status_code}): {res.text}")

            data = res.json()
            job_id = data.get("id")
            if not job_id:
                raise RuntimeError(f"LTX Video response missing job ID: {data}")

            return job_id

    @classmethod
    async def poll_until_complete(
        cls,
        job_id: str,
        max_wait_seconds: int = 180,
        poll_interval: int = 4
    ) -> Dict[str, Any]:
        """
        Polls GET /v2/text-to-video/{id} until 'completed' or failure.
        """
        headers = cls._get_headers()
        elapsed = 0
        async with httpx.AsyncClient(timeout=20.0) as client:
            while elapsed < max_wait_seconds:
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

                res = await client.get(f"{cls.BASE_URL}/v2/text-to-video/{job_id}", headers=headers)
                if res.status_code != 200:
                    logger.warning(f"[LTXVideo] Poll returned status {res.status_code}: {res.text}")
                    continue

                project = res.json()
                status = project.get("status")

                if status == "completed":
                    result = project.get("result", {})
                    download_url = result.get("video_url")
                    if not download_url:
                        raise RuntimeError(f"LTX Video completed but missing download URL: {project}")

                    logger.info(f"[LTXVideo] Job {job_id} completed successfully in {elapsed}s.")
                    return {
                        "job_id": job_id,
                        "status": "completed",
                        "download_url": download_url,
                        "completed_at": project.get("completed_at"),
                        "elapsed_seconds": elapsed
                    }
                elif status in ("failed", "error"):
                    err = project.get("error") or "Unknown LTX Video generation failure"
                    raise RuntimeError(f"LTX Video job {job_id} failed: {err}")

                logger.info(f"[LTXVideo] Job {job_id} rendering (status={status}, elapsed={elapsed}s)...")

        raise TimeoutError(f"LTX Video generation timed out after {max_wait_seconds} seconds.")

    @classmethod
    async def generate_and_persist_video(
        cls,
        prompt: str,
        brand: Optional[BrandProfile] = None,
        aspect_ratio: str = "9:16",
        duration_seconds: int = 30,
        voice: Optional[str] = None,
        voice_script: Optional[str] = None,
        script_scenes: Optional[list] = None,
        organization_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        End-to-End Orchestrated Video Workflow:
        1. Formulates brand-rich prompt and commercial voiceover script.
        2. Calls LTX Video API for a 5-second cinematic neural clip (~$0.45 / ₹38.70).
        3. Generates high-fidelity Microsoft Edge Neural TTS narration.
        4. Muxes into a 30s broadcast-ready commercial reel with FFmpeg.
        5. Persists to local disk and Cloudinary CDN.
        6. Logs actual cost and performance metadata.
        """
        cls._ensure_directories()
        brand_name = brand.name if brand and brand.name else "Brand"
        brand_tone = brand.tone_of_voice if brand and brand.tone_of_voice else "inspiring"
        target_dur = max(10, min(int(duration_seconds), 30))

        # 1. Compose voiceover narration script
        composed_voiceover = voice_script or VoiceService.compose_commercial_voiceover(
            prompt=prompt,
            brand_name=brand_name,
            brand_tagline=brand.tagline if brand else "",
            brand_tone=brand_tone,
            script_scenes=script_scenes,
            target_duration=target_dur,
            variation_seed=uuid.uuid4().hex[:8]
        )

        target_voice_id = VoiceService.resolve_voice(
            voice_preference=voice,
            brand_tone=brand_tone,
            seed=f"{prompt[:30]}-{brand_name}"
        )

        voice_filename = f"ltx_voice_{uuid.uuid4().hex[:12]}.mp3"
        voice_path = os.path.join(cls.AI_VIDEOS_DIR, voice_filename)
        actual_voice_dur = target_dur
        has_voice = False
        try:
            actual_voice_dur = await VoiceService.synthesize_speech(composed_voiceover, voice_path, voice=target_voice_id)
            has_voice = os.path.exists(voice_path) and os.path.getsize(voice_path) > 1000
        except Exception as v_err:
            logger.warning(f"[LTXVideo] Voiceover synthesis notice: {v_err}")

        # 2. Enrich prompt for LTX Video
        clean_user_prompt = (prompt or f"Cinematic product showcase for {brand_name}").strip()
        enriched_prompt = (
            f"Cinematic commercial for {brand_name}: {clean_user_prompt}. "
            f"Ultra realistic 8k, dynamic studio lighting, smooth professional camera motion, photorealistic masterpiece, clean footage, no watermark, no logo, no text"
        )[:3000]

        # Resolution based on aspect ratio
        res_map = {
            "9:16": "720x1280",
            "16:9": "1280x720",
            "1:1": "1024x1024"
        }
        res_str = res_map.get(aspect_ratio, "1280x720")

        # 3. Submit LTX job
        clip_gen_duration = 5
        job_id = await cls.create_text_to_video_job(
            prompt=enriched_prompt,
            duration_seconds=clip_gen_duration,
            resolution=res_str,
            model="ltx-2-5-fast"
        )

        # 4. Poll until completed
        completed_info = await cls.poll_until_complete(job_id, max_wait_seconds=180, poll_interval=4)
        download_url = completed_info["download_url"]

        # 5. Download the raw LTX clip
        raw_clip_filename = f"ltx_raw_{uuid.uuid4().hex[:12]}.mp4"
        raw_clip_path = os.path.join(cls.AI_VIDEOS_DIR, raw_clip_filename)

        async with httpx.AsyncClient(timeout=60.0) as client:
            v_res = await client.get(download_url)
            if v_res.status_code != 200:
                raise RuntimeError(f"Failed to download rendered video from LTX: {v_res.status_code}")
            with open(raw_clip_path, "wb") as f:
                f.write(v_res.content)

        # 6. Assemble Full 30-Second Multi-Scene Cinematic Movie Reel combining AI Prompt, Neural Motion & Image
        from app.services.video_generation_service import VideoGenerationService
        import asyncio
        logger.info(f"[LTXVideo] Assembling continuous 30-second cinematic movie reel with LTX neural video clip...")
        movie_result = await asyncio.to_thread(
            VideoGenerationService.render_branded_promotional_video,
            prompt=prompt,
            brand=brand,
            aspect_ratio=aspect_ratio,
            duration_seconds=target_dur,
            fps=24,
            voice=target_voice_id,
            voice_script=composed_voiceover,
            script_scenes=script_scenes,
            description=prompt,
            neural_video_path=raw_clip_path
        )

        # Cleanup temporary files
        for tmp_f in [raw_clip_path, voice_path]:
            if os.path.exists(tmp_f):
                try:
                    os.remove(tmp_f)
                except Exception:
                    pass

        # Cost tracking details
        cost_meta = cls.calculate_cost(clip_gen_duration)

        return {
            "video_url": movie_result.get("video_url"),
            "file_path": movie_result.get("video_url"),
            "local_path": movie_result.get("local_path"),
            "file_name": movie_result.get("file_name"),
            "file_size_bytes": movie_result.get("file_size_bytes", 0),
            "duration_seconds": movie_result.get("duration_seconds", target_dur),
            "aspect_ratio": aspect_ratio,
            "mime_type": "video/mp4",
            "model_name": "ltx-2-5-fast",
            "provider": "ltx-video",
            "cost_usd": cost_meta["cost_usd"],
            "cost_inr": cost_meta["cost_inr"],
            "prompt": enriched_prompt,
            "acts_breakdown": movie_result.get("acts_breakdown", []),
            "voiceover_script": movie_result.get("voiceover_script", composed_voiceover),
            "has_voiceover": movie_result.get("has_voiceover", has_voice),
            "voice_id": target_voice_id,
            "brand_name": brand_name,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
