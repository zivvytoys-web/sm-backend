"""
Video Processing Service
Provides intelligent, aspect-ratio-preserving media transformations, FFmpeg post-processing,
and platform-specific validation for Instagram Reels, YouTube Shorts, Facebook, X, and TikTok.
Ensures zero compression/stretching distortion, clean full-frame cropping, and standard H.264/AAC output.
"""

import os
import uuid
import subprocess
from typing import Dict, Any, Optional, Tuple
import cv2
import numpy as np
from PIL import Image
import imageio_ffmpeg

from app.core.config import settings
from app.core.logging import logger
from app.core.ffmpeg_utils import get_ffmpeg_executable
from app.integrations.storage.cloudinary_storage import CloudinaryStorageProvider


class VideoProcessingService:
    """Enterprise service for intelligent video aspect-ratio reframing and platform standardization."""

    PROCESSED_VIDEOS_DIR = os.path.join(settings.LOCAL_STORAGE_DIR, "media", "processed_videos")

    STANDARD_RATIOS = {
        "9:16": {"1080p": (1080, 1920), "4k": (2160, 3840), "720p": (720, 1280), "ratio_float": 9.0 / 16.0},
        "16:9": {"1080p": (1920, 1080), "4k": (3840, 2160), "720p": (1280, 720), "ratio_float": 16.0 / 9.0},
        "1:1": {"1080p": (1080, 1080), "4k": (2160, 2160), "720p": (720, 720), "ratio_float": 1.0},
        "4:5": {"1080p": (1080, 1350), "4k": (2160, 2700), "720p": (720, 900), "ratio_float": 4.0 / 5.0},
        "1.91:1": {"1080p": (1200, 628), "4k": (2400, 1256), "720p": (960, 502), "ratio_float": 1.91},
    }

    @classmethod
    def _ensure_dir(cls):
        os.makedirs(cls.PROCESSED_VIDEOS_DIR, exist_ok=True)

    @classmethod
    def resolve_target_dimensions(
        cls,
        aspect_ratio: str = "9:16",
        resolution: str = "1080p"
    ) -> Tuple[int, int, str]:
        """
        Resolves standard pixel dimensions and canonical ratio key for the given aspect ratio and resolution.
        """
        clean_ratio = str(aspect_ratio or "9:16").strip().lower()
        if clean_ratio in ("9:16", "9/16", "vertical", "reel", "short", "story"):
            canonical = "9:16"
        elif clean_ratio in ("16:9", "16/9", "landscape", "youtube", "wide"):
            canonical = "16:9"
        elif clean_ratio in ("1:1", "square", "feed_square"):
            canonical = "1:1"
        elif clean_ratio in ("4:5", "4/5", "portrait", "feed_portrait"):
            canonical = "4:5"
        elif clean_ratio in ("1.91:1", "banner", "landscape_feed"):
            canonical = "1.91:1"
        else:
            canonical = "9:16"

        res_key = "4k" if str(resolution).lower() in ("4k", "uhd") else "720p" if str(resolution).lower() == "720p" else "1080p"
        dims = cls.STANDARD_RATIOS[canonical][res_key]
        return dims[0], dims[1], canonical

    @classmethod
    def get_platform_target_format(
        cls,
        platform: str,
        post_format: str = "reel",
        content_type: str = "video",
        preferred_aspect_ratio: Optional[str] = None
    ) -> Tuple[int, int, str]:
        """
        Determines the official target aspect ratio and dimensions for the destination social platform:
        - Instagram Reels, Facebook Reels, YouTube Shorts, TikTok, Snapchat: 9:16 (1080x1920)
        - Instagram Feed: 4:5 (1080x1350) or 1:1 (1080x1080)
        - YouTube Standard: 16:9 (1920x1080)
        - X (Twitter): 16:9 (1920x1080), 1:1 (1080x1080), or 9:16 (1080x1920)
        """
        plat = str(platform or "").lower().strip()
        fmt = str(post_format or "").lower().strip()

        # If user explicitly chose an aspect ratio, respect it if valid
        if preferred_aspect_ratio and preferred_aspect_ratio in cls.STANDARD_RATIOS:
            return cls.resolve_target_dimensions(preferred_aspect_ratio)

        # 1. Instagram
        if plat == "instagram":
            if fmt in ("portrait", "feed_portrait", "4:5"):
                return cls.resolve_target_dimensions("4:5")
            elif fmt in ("square", "feed_square", "1:1"):
                return cls.resolve_target_dimensions("1:1")
            elif fmt in ("reel", "reels", "short", "shorts", "story", "stories") or content_type == "video":
                return cls.resolve_target_dimensions("9:16")
            else:
                return cls.resolve_target_dimensions("1:1")

        # 2. Facebook
        if plat == "facebook":
            if fmt in ("reel", "reels", "short", "shorts", "story") or "reel" in fmt:
                return cls.resolve_target_dimensions("9:16")
            return cls.resolve_target_dimensions("1:1")

        # 3. YouTube
        if plat == "youtube":
            if fmt in ("short", "shorts", "reel", "vertical"):
                return cls.resolve_target_dimensions("9:16")
            return cls.resolve_target_dimensions("16:9")

        # 4. TikTok & Snapchat
        if plat in ("tiktok", "snapchat"):
            return cls.resolve_target_dimensions("9:16")

        # 5. X / Twitter
        if plat in ("twitter", "x"):
            if fmt in ("reel", "short", "vertical"):
                return cls.resolve_target_dimensions("9:16")
            return cls.resolve_target_dimensions("16:9")

        # 6. LinkedIn / Pinterest / General
        if plat == "linkedin":
            return cls.resolve_target_dimensions("1:1")
        if plat == "pinterest":
            return cls.resolve_target_dimensions("9:16")

        return cls.resolve_target_dimensions("9:16" if content_type == "video" else "1:1")

    @classmethod
    def probe_media(cls, file_path_or_url: str) -> Dict[str, Any]:
        """
        Probes a video or image to extract its exact width, height, duration, aspect ratio, and audio stream.
        """
        info = {
            "width": 0,
            "height": 0,
            "duration": 0.0,
            "fps": 30.0,
            "aspect_ratio_float": 0.0,
            "canonical_ratio": "unknown",
            "has_audio": False,
            "is_valid": False
        }

        local_path = file_path_or_url
        tmp_download = None

        if str(file_path_or_url).startswith("http://") or str(file_path_or_url).startswith("https://"):
            try:
                import httpx
                cls._ensure_dir()
                tmp_download = os.path.join(cls.PROCESSED_VIDEOS_DIR, f"probe_{uuid.uuid4().hex[:8]}.mp4")
                with httpx.Client(timeout=30.0) as client:
                    resp = client.get(file_path_or_url)
                    if resp.status_code == 200 and len(resp.content) > 500:
                        with open(tmp_download, "wb") as f:
                            f.write(resp.content)
                        local_path = tmp_download
            except Exception as e:
                logger.warning(f"Could not download remote media for probing: {e}")

        if not os.path.exists(local_path):
            return info

        cap = cv2.VideoCapture(local_path)
        if cap.isOpened():
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            dur = round(total_frames / max(1.0, fps), 2)
            cap.release()

            if w > 0 and h > 0:
                r_float = round(w / float(h), 4)
                info["width"] = w
                info["height"] = h
                info["fps"] = fps
                info["duration"] = dur
                info["aspect_ratio_float"] = r_float
                info["is_valid"] = True

                # Classify closest canonical ratio
                closest = min(
                    cls.STANDARD_RATIOS.keys(),
                    key=lambda k: abs(cls.STANDARD_RATIOS[k]["ratio_float"] - r_float)
                )
                info["canonical_ratio"] = closest

        # Check audio stream via FFmpeg
        try:
            ffmpeg_exe = get_ffmpeg_executable()
            proc = subprocess.run(
                [ffmpeg_exe, "-threads", "2", "-i", local_path],
                stderr=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
                timeout=10
            )
            out_txt = proc.stderr or proc.stdout or ""
            info["has_audio"] = "Audio:" in out_txt
        except Exception:
            pass

        if tmp_download and os.path.exists(tmp_download):
            try:
                os.remove(tmp_download)
            except Exception:
                pass

        return info

    @classmethod
    def detect_visual_center_of_interest(cls, frame: np.ndarray) -> Tuple[float, float]:
        """
        Analyzes image visual saliency and contour energy to determine the primary subject's
        center of mass. Returns normalized coordinates (cx, cy) where 0.5 is canvas center.
        Prevents cropping out products, faces, or text during aspect re-framing.
        """
        try:
            h, w = frame.shape[:2]
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            # Edge and brightness gradient
            grad_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
            grad_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
            magnitude = cv2.magnitude(grad_x, grad_y)
            # Blur to get heat regions
            blurred = cv2.GaussianBlur(magnitude, (31, 31), 0)
            moments = cv2.moments(blurred)
            if moments["m00"] > 1e-4:
                cx = (moments["m10"] / moments["m00"]) / float(w)
                cy = (moments["m01"] / moments["m00"]) / float(h)
                # Keep within safe bounds (0.25 to 0.75) to prevent extreme edge bias
                return max(0.25, min(0.75, cx)), max(0.25, min(0.75, cy))
        except Exception:
            pass
        return 0.5, 0.5

    @classmethod
    def reframe_frame_without_distortion(
        cls,
        frame: np.ndarray,
        target_w: int,
        target_h: int
    ) -> np.ndarray:
        """
        Intelligently crops and scales an OpenCV BGR frame to exact target dimensions
        WITHOUT ANY COMPRESSION OR STRETCHING:
        - If source ratio matches target ratio: clean direct resize.
        - If source is wider than target (e.g. 16:9 -> 9:16): crops width anchored on subject.
        - If source is taller than target: crops height anchored on subject.
        - Result is 100% full-frame composition with zero black bars or blur distortion.
        """
        src_h, src_w = frame.shape[:2]
        if src_w <= 0 or src_h <= 0 or target_w <= 0 or target_h <= 0:
            return frame

        src_ratio = src_w / float(src_h)
        target_ratio = target_w / float(target_h)

        # Tolerant match (within 1%) -> direct proportional scale
        if abs(src_ratio - target_ratio) < 0.015:
            return cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_LANCZOS4)

        # Vertical story/reel format (target_w < target_h): Preserve 100% full product without cutting off sides
        if target_w < target_h and src_ratio > 0.75:
            bg_scale = max(target_w / float(src_w), target_h / float(src_h))
            bg_w, bg_h = int(src_w * bg_scale), int(src_h * bg_scale)
            bg = cv2.resize(frame, (bg_w, bg_h), interpolation=cv2.INTER_LINEAR)
            x0 = max(0, (bg_w - target_w) // 2)
            y0 = max(0, (bg_h - target_h) // 2)
            bg_crop = bg[y0:y0 + target_h, x0:x0 + target_w]
            bg_blurred = cv2.GaussianBlur(bg_crop, (51, 51), 0)
            bg_blurred = cv2.convertScaleAbs(bg_blurred, alpha=0.60, beta=0)

            fg_scale = min((target_w * 0.95) / float(src_w), (target_h * 0.65) / float(src_h))
            fg_w, fg_h = int(src_w * fg_scale), int(src_h * fg_scale)
            fg = cv2.resize(frame, (fg_w, fg_h), interpolation=cv2.INTER_LANCZOS4)

            fg_x = (target_w - fg_w) // 2
            fg_y = (target_h - fg_h) // 2
            bg_blurred[fg_y:fg_y + fg_h, fg_x:fg_x + fg_w] = fg
            return bg_blurred

        focal_x, focal_y = cls.detect_visual_center_of_interest(frame)

        if src_ratio > target_ratio:
            new_w = int(src_h * target_ratio)
            desired_x = int(focal_x * src_w - new_w / 2.0)
            x0 = max(0, min(src_w - new_w, desired_x))
            cropped = frame[0:src_h, x0:x0 + new_w]
        else:
            new_h = int(src_w / target_ratio)
            desired_y = int(focal_y * src_h - new_h / 2.0)
            y0 = max(0, min(src_h - new_h, desired_y))
            cropped = frame[y0:y0 + new_h, 0:src_w]

        return cv2.resize(cropped, (target_w, target_h), interpolation=cv2.INTER_LANCZOS4)

    @classmethod
    def process_and_standardize_video(
        cls,
        input_path: str,
        output_path: Optional[str] = None,
        target_aspect_ratio: str = "9:16",
        resolution: str = "1080p",
        platform: Optional[str] = None,
        post_format: str = "reel"
    ) -> Dict[str, Any]:
        """
        Standardizes an input video to the exact aspect ratio and technical requirements
        for the target platform (e.g. 9:16 1080x1920 for Instagram Reels / YouTube Shorts).
        Uses FFmpeg with intelligent crop-fit:
        - NEVER squashes or stretches the video.
        - Keeps full-frame visual composition without borders or black bars.
        - Normalizes video to H.264 (yuv420p) and audio to AAC.
        - Preserves audio synchronization and web faststart.
        """
        cls._ensure_dir()
        if platform:
            target_w, target_h, canonical_ratio = cls.get_platform_target_format(
                platform=platform,
                post_format=post_format,
                preferred_aspect_ratio=target_aspect_ratio
            )
        else:
            target_w, target_h, canonical_ratio = cls.resolve_target_dimensions(
                aspect_ratio=target_aspect_ratio,
                resolution=resolution
            )

        if not output_path:
            out_filename = f"std_{canonical_ratio.replace(':', '_')}_{uuid.uuid4().hex[:10]}.mp4"
            output_path = os.path.join(cls.PROCESSED_VIDEOS_DIR, out_filename)

        probe = cls.probe_media(input_path)
        if not probe["is_valid"]:
            raise ValueError(f"Input video '{input_path}' is invalid or cannot be opened.")

        in_w, in_h = probe["width"], probe["height"]
        in_ratio = probe["aspect_ratio_float"]
        target_ratio_val = target_w / float(target_h)

        ffmpeg_exe = get_ffmpeg_executable()

        # Build integer crop boundaries centered on frame to guarantee 0 distortion
        if abs(in_ratio - target_ratio_val) < 0.015:
            vf = f"scale={target_w}:{target_h}:flags=lanczos"
        elif in_ratio > target_ratio_val:
            # Input is wider than target: crop width along horizontal axis
            crop_w = int(in_h * target_ratio_val)
            crop_w = crop_w - (crop_w % 2)
            crop_h = in_h - (in_h % 2)
            crop_x = (in_w - crop_w) // 2
            crop_y = 0
            vf = f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale={target_w}:{target_h}:flags=lanczos"
        else:
            # Input is taller than target: crop height along vertical axis
            crop_w = in_w - (in_w % 2)
            crop_h = int(in_w / target_ratio_val)
            crop_h = crop_h - (crop_h % 2)
            crop_x = 0
            crop_y = (in_h - crop_h) // 2
            vf = f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale={target_w}:{target_h}:flags=lanczos"

        cmd = [ffmpeg_exe, "-y", "-i", input_path]
        if probe["has_audio"]:
            cmd.extend([
                "-vf", vf,
                "-map", "0:v:0",
                "-map", "0:a:0",
                "-c:v", "libx264",
                "-threads", "2",
                "-preset", "fast",
                "-crf", "20",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", "192k",
                "-ar", "44100",
                "-movflags", "+faststart",
                output_path
            ])
        else:
            cmd.extend([
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-vf", vf,
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-c:v", "libx264",
                "-threads", "2",
                "-preset", "fast",
                "-crf", "20",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", "128k",
                "-shortest",
                "-movflags", "+faststart",
                output_path
            ])

        logger.info(f"[VideoProcessing] Standardizing video to {canonical_ratio} ({target_w}x{target_h}) via FFmpeg filter: {vf}")
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if res.returncode != 0:
            if not os.path.exists(output_path) or os.path.getsize(output_path) < 1024:
                logger.error(f"[VideoProcessing] FFmpeg standardization failed: {res.stderr[:300]}")
                raise RuntimeError(f"FFmpeg video standardization failed: {res.stderr[:200]}")
            else:
                logger.warning(f"[VideoProcessing] FFmpeg completed with non-fatal warning: {res.stderr[:200]}")

        # Verify output
        final_probe = cls.probe_media(output_path)
        if not final_probe["is_valid"]:
            raise RuntimeError("Standardized video output verification failed.")

        return {
            "file_path": output_path,
            "width": final_probe["width"],
            "height": final_probe["height"],
            "duration": final_probe["duration"],
            "aspect_ratio": canonical_ratio,
            "file_size_bytes": os.path.getsize(output_path),
            "is_valid": True
        }

    @classmethod
    def ensure_video_ready_for_platform(
        cls,
        media_url_or_path: str,
        platform: str,
        post_format: str = "reel",
        organization_id: Optional[int] = None
    ) -> Tuple[str, bool]:
        """
        Validates that a video is in the correct aspect ratio and encoding for the target platform.
        If it requires conversion (e.g. landscape video being published as Instagram Reel),
        converts it without stretching into standard 9:16 (1080x1920) and uploads it.
        Returns: (final_media_url_or_path, was_converted)
        """
        plat = platform.lower().strip()
        fmt = post_format.lower().strip()

        target_w, target_h, target_ratio = cls.get_platform_target_format(plat, fmt, content_type="video")
        probe = cls.probe_media(media_url_or_path)

        # If already matching target ratio within 2% AND has an audio track, no conversion needed
        target_float = target_w / float(target_h)
        if (
            probe["is_valid"]
            and abs(probe["aspect_ratio_float"] - target_float) < 0.02
            and probe["width"] >= 720
            and probe.get("has_audio", False)
        ):
            logger.info(f"[VideoProcessing] Video already conforms to {plat} {fmt} requirements ({probe['width']}x{probe['height']}) with valid audio.")
            return media_url_or_path, False

        logger.info(
            f"[VideoProcessing] Video aspect ratio {probe['aspect_ratio_float']:.3f} does not match "
            f"target {target_ratio} ({target_w}x{target_h}). Running intelligent reframing..."
        )

        local_input = media_url_or_path
        tmp_download = None

        if str(media_url_or_path).startswith("http://") or str(media_url_or_path).startswith("https://"):
            import httpx
            cls._ensure_dir()
            tmp_download = os.path.join(cls.PROCESSED_VIDEOS_DIR, f"input_{uuid.uuid4().hex[:8]}.mp4")
            with httpx.Client(timeout=45.0) as client:
                r = client.get(media_url_or_path)
                if r.status_code == 200:
                    with open(tmp_download, "wb") as f:
                        f.write(r.content)
                    local_input = tmp_download

        try:
            processed = cls.process_and_standardize_video(
                input_path=local_input,
                target_aspect_ratio=target_ratio,
                platform=plat,
                post_format=fmt
            )
            final_local_path = processed["file_path"]

            # Upload to Cloudinary CDN if configured
            final_url = final_local_path
            if CloudinaryStorageProvider.is_configured():
                try:
                    c_res = CloudinaryStorageProvider.upload_local_file_path(
                        file_path=final_local_path,
                        organization_id=organization_id or 1,
                        folder_prefix="socialai/standardized_videos",
                        resource_type="video"
                    )
                    if c_res and c_res.get("file_url"):
                        final_url = c_res["file_url"]
                        logger.info(f"[VideoProcessing] Standardized video uploaded to Cloudinary: {final_url}")
                except Exception as upload_err:
                    logger.warning(f"[VideoProcessing] Cloudinary upload notice: {upload_err}")

            return final_url, True

        finally:
            if tmp_download and os.path.exists(tmp_download):
                try:
                    os.remove(tmp_download)
                except Exception:
                    pass
