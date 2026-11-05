import os
import uuid
import asyncio
from typing import Dict, Any, Optional
import httpx
from app.core.config import settings
from app.core.logging import logger
from app.integrations.storage.cloudinary_storage import CloudinaryStorageProvider


class MagicHourVideoService:
    """
    Dedicated client for Magic Hour AI Video Generation API (api.magichour.ai).
    Supports:
    1. Image-to-Video: Converts static brand images / logos into cinematic animated videos
    2. Text-to-Video: Generates high-converting promotional videos directly from creative prompts
    3. Automatic async polling and permanent Cloudinary CDN / local persistence
    """
    BASE_URL = "https://api.magichour.ai"

    @classmethod
    def get_api_key(cls) -> Optional[str]:
        return getattr(settings, "MAGIC_HOUR_API_KEY", None) or os.getenv("MAGIC_HOUR_API_KEY")

    @classmethod
    def is_configured(cls) -> bool:
        key = cls.get_api_key()
        return bool(key and key.strip().startswith("mhk_"))

    @classmethod
    def _get_headers(cls) -> Dict[str, str]:
        key = cls.get_api_key()
        if not key:
            raise ValueError("Magic Hour API Key is not configured. Please add MAGIC_HOUR_API_KEY to your environment.")
        return {
            "Authorization": f"Bearer {key.strip()}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    @classmethod
    async def upload_image_asset(cls, image_url_or_path: str) -> str:
        """
        Ensures the image is uploaded to Magic Hour storage and returns the internal file_path reference.
        """
        # If it's already a public web URL, check if Magic Hour requires upload or can use it
        image_bytes = None
        ext = "png"

        if image_url_or_path.startswith("http://") or image_url_or_path.startswith("https://"):
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.get(image_url_or_path)
                if res.status_code == 200:
                    image_bytes = res.content
                    if "image/jpeg" in res.headers.get("Content-Type", "") or image_url_or_path.lower().endswith((".jpg", ".jpeg")):
                        ext = "jpg"
                    elif "image/webp" in res.headers.get("Content-Type", "") or image_url_or_path.lower().endswith(".webp"):
                        ext = "webp"
        elif os.path.isfile(image_url_or_path):
            with open(image_url_or_path, "rb") as f:
                image_bytes = f.read()
            if image_url_or_path.lower().endswith((".jpg", ".jpeg")):
                ext = "jpg"
            elif image_url_or_path.lower().endswith(".webp"):
                ext = "webp"

        if not image_bytes:
            raise ValueError(f"Unable to read image asset for Magic Hour video generation: {image_url_or_path}")

        headers = cls._get_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. Request presigned upload URL
            req_payload = {
                "items": [
                    {
                        "type": "image",
                        "extension": ext
                    }
                ]
            }
            res = await client.post(f"{cls.BASE_URL}/v1/files/upload-urls", json=req_payload, headers=headers)
            if res.status_code != 200:
                raise RuntimeError(f"Magic Hour upload URL request failed: {res.status_code} {res.text}")

            upload_info = res.json()["items"][0]
            upload_url = upload_info["upload_url"]
            file_path = upload_info["file_path"]

            # 2. PUT binary image bytes to presigned URL
            upload_res = await client.put(upload_url, content=image_bytes)
            if upload_res.status_code not in (200, 201):
                raise RuntimeError(f"Failed to upload image to Magic Hour: {upload_res.status_code} {upload_res.text}")

            return file_path

    @classmethod
    async def create_image_to_video_job(
        cls,
        image_url_or_path: str,
        prompt: Optional[str] = None,
        duration_seconds: int = 30,
        # 480p is available on the current Magic Hour subscription; higher
        # resolutions require a plan upgrade and otherwise return HTTP 402.
        resolution: str = "480p",
        aspect_ratio: str = "9:16",
        project_name: str = "AI Social Reel"
    ) -> str:
        """
        Submits an image-to-video generation job and returns the project ID.
        """
        headers = cls._get_headers()
        magic_hour_file_path = await cls.upload_image_asset(image_url_or_path)

        payload = {
            "name": project_name,
            "assets": {
                "image_file_path": magic_hour_file_path
            },
            # The current configured plan supports short clips (up to 10s).
            # Do not silently coerce a 10-second request into a 25-second
            # render, which causes the provider to reject it as a paid-plan
            # feature.
            "end_seconds": float(max(4, min(duration_seconds, 10))),
            "resolution": resolution if resolution in ["480p", "720p", "1080p"] else "480p"
        }
        if prompt and prompt.strip():
            payload["prompt"] = prompt.strip()

        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.post(f"{cls.BASE_URL}/v1/image-to-video", json=payload, headers=headers)
            if res.status_code not in (200, 201):
                raise RuntimeError(f"Magic Hour image-to-video failed ({res.status_code}): {res.text}")

            data = res.json()
            project_id = data.get("id")
            if not project_id:
                raise RuntimeError(f"Magic Hour response missing project ID: {data}")
            return project_id

    @classmethod
    async def poll_until_complete(cls, project_id: str, max_wait_seconds: int = 180, poll_interval: int = 5) -> Dict[str, Any]:
        """
        Polls Magic Hour GET /v1/video-projects/{id} until 'complete' or failure.
        """
        headers = cls._get_headers()
        elapsed = 0
        async with httpx.AsyncClient(timeout=20.0) as client:
            while elapsed < max_wait_seconds:
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

                res = await client.get(f"{cls.BASE_URL}/v1/video-projects/{project_id}", headers=headers)
                if res.status_code != 200:
                    logger.warning(f"Magic Hour poll status returned {res.status_code}: {res.text}")
                    continue

                project = res.json()
                status = project.get("status")

                if status == "complete":
                    downloads = project.get("downloads", [])
                    if not downloads or not downloads[0].get("url"):
                        raise RuntimeError(f"Magic Hour project completed but returned no download URL: {project}")
                    return {
                        "project_id": project_id,
                        "status": "complete",
                        "download_url": downloads[0]["url"],
                        "filename": downloads[0].get("filename", f"magichour_{project_id}.mp4"),
                        "duration_seconds": project.get("duration", 5.0),
                        "resolution": project.get("resolution", "720p"),
                        "raw": project
                    }
                elif status in ("error", "canceled"):
                    err = project.get("error") or "Unknown Magic Hour generation failure"
                    raise RuntimeError(f"Magic Hour project {project_id} failed with status '{status}': {err}")

                logger.info(f"Magic Hour video {project_id} still rendering (status={status}, elapsed={elapsed}s)...")

        raise TimeoutError(f"Magic Hour video generation timed out after {max_wait_seconds} seconds.")

    @classmethod
    async def generate_and_persist_video(
        cls,
        image_url_or_path: str,
        prompt: str,
        aspect_ratio: str = "9:16",
        duration_seconds: int = 30,
        organization_id: int = 1
    ) -> Dict[str, Any]:
        """
        High-level pipeline method:
        1. Submits image-to-video request to Magic Hour
        2. Polls until completed
        3. Downloads MP4 output
        4. Uploads to Cloudinary / local storage
        5. Returns completed asset details
        """
        logger.info(f"Initiating Magic Hour AI video generation with prompt='{prompt[:40]}...'")
        project_id = await cls.create_image_to_video_job(
            image_url_or_path=image_url_or_path,
            prompt=prompt,
            duration_seconds=duration_seconds,
            aspect_ratio=aspect_ratio,
            project_name=f"Social AI - {prompt[:25]}"
        )

        completed_info = await cls.poll_until_complete(project_id, max_wait_seconds=240, poll_interval=6)
        download_url = completed_info["download_url"]

        # Download the MP4 bytes
        async with httpx.AsyncClient(timeout=60.0) as client:
            v_res = await client.get(download_url)
            if v_res.status_code != 200:
                raise RuntimeError(f"Failed to download rendered video from Magic Hour: {v_res.status_code}")
            video_bytes = v_res.content

        output_dir = os.path.join(settings.LOCAL_STORAGE_DIR, "media", "ai_videos")
        os.makedirs(output_dir, exist_ok=True)
        local_filename = f"magichour_{uuid.uuid4().hex[:12]}.mp4"
        local_path = os.path.join(output_dir, local_filename)

        with open(local_path, "wb") as f:
            f.write(video_bytes)

        # Reject empty/corrupt outputs before they reach the media library.
        # Magic Hour's metadata can occasionally report zero while the file
        # itself is still valid, so inspect the downloaded MP4 as well.
        if len(video_bytes) < 4096:
            raise RuntimeError("Magic Hour returned an empty or incomplete video file.")
        try:
            import cv2
            capture = cv2.VideoCapture(local_path)
            frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
            fps = capture.get(cv2.CAP_PROP_FPS)
            capture.release()
            actual_duration = (frame_count / fps) if fps and fps > 0 else 0
            if actual_duration <= 0:
                raise RuntimeError("Magic Hour returned a video with zero playable duration.")
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Unable to validate Magic Hour MP4: {exc}") from exc

        file_url = f"/uploads/media/ai_videos/{local_filename}"

        # Persist to Cloudinary CDN; local-only URLs cannot be played by
        # scheduled social publishers.
        if not CloudinaryStorageProvider.is_configured():
            raise RuntimeError("Cloudinary storage is not configured for generated videos.")
        if CloudinaryStorageProvider.is_configured():
            try:
                c_res = CloudinaryStorageProvider.upload_local_file_path(
                    file_path=local_path,
                    organization_id=organization_id,
                    folder_prefix="socialai/ai_videos",
                    resource_type="video"
                )
                if c_res and c_res.get("file_url"):
                    file_url = c_res["file_url"]
            except Exception as e:
                raise RuntimeError(f"Cloudinary upload of Magic Hour video failed: {e}") from e

        return {
            "video_url": file_url,
            "file_path": file_url,
            "local_path": local_path,
            "file_name": local_filename,
            "file_size_bytes": len(video_bytes),
            "duration_seconds": max(float(completed_info.get("duration_seconds") or 0), actual_duration),
            "aspect_ratio": aspect_ratio,
            "resolution": completed_info.get("resolution", "720p"),
            "provider": "magichour-ai",
            "model_name": "magichour-image-to-video",
            "prompt": prompt
        }
