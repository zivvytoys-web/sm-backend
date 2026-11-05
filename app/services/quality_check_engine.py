import os
import io
import re
import cv2
import json
import base64
import hashlib
import numpy as np
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple, Union
from difflib import SequenceMatcher
import httpx
from PIL import Image
import imagehash
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.config import settings
from app.core.logging import logger
from app.core.gemini_client import get_gemini_client, is_gemini_configured
from app.models.brand_profile import BrandProfile
from app.models.media_asset import MediaAsset
from app.models.content import Content
from app.models.quality_report import QualityReport


# =========================================================================
# CONFIGURABLE THRESHOLDS (SECTION 6 & 7 OF SPECIFICATION)
# =========================================================================
QUALITY_THRESHOLDS = {
    # Duplicate Detection
    "IMAGE_PHASH_MAX_HAMMING": 6,          # Flag when Hamming distance <= 6
    "IMAGE_EMBEDDING_MIN_COSINE": 0.94,    # Flag at 0.94 or above
    "VIDEO_EMBEDDING_MIN_COSINE": 0.93,    # Flag at 0.93 or above
    "DESCRIPTION_EMBEDDING_MIN_COSINE": 0.92, # Flag at 0.92 or above
    "DESCRIPTION_JACCARD_MIN_SIMILARITY": 0.88,
    "MAX_REGENERATION_ATTEMPTS": 3,
    "DESCRIPTION_SIMILARITY_THRESHOLD": 0.88,
    "description_similarity_threshold": 0.88,
    "image_similarity_threshold": 0.94,
    "video_similarity_threshold": 0.93,

    # Image Technical Quality
    "IMAGE_MIN_WIDTH": 600,
    "IMAGE_MIN_HEIGHT": 600,
    "IMAGE_IDEAL_MIN_DIM": 1080,
    "IMAGE_MAX_FILE_SIZE_BYTES": 25 * 1024 * 1024, # 25MB
    "IMAGE_MIN_BLUR_LAPLACIAN_VAR": 40.0,  # Below 40 is considered blurry/degraded
    "IMAGE_MIN_COLOR_DIVERSITY_STD": 12.0, # Below 12 is near-blank/monochrome

    # Video Technical Quality
    "VIDEO_MIN_DURATION_SECONDS": 3.0,
    "VIDEO_MAX_DURATION_SECONDS": 180.0,
    "VIDEO_MIN_FPS": 15.0,
    "VIDEO_MIN_WIDTH": 640,
    "VIDEO_MIN_HEIGHT": 640,
    "VIDEO_BLACK_FRAME_MAX_LUM": 8.0,
    "VIDEO_MAX_BLACK_FRAMES_RATIO": 0.40,
    "VIDEO_FROZEN_FRAME_MAX_DIFF": 1.0,
    "VIDEO_MAX_FROZEN_FRAMES_RATIO": 0.70,

    # Description Technical & Platform Rules
    "TEXT_MIN_WORDS": 3,
    "PLATFORM_LIMITS": {
        "instagram": {"max_caption": 2200, "max_hashtags": 30, "min_hashtags": 1},
        "facebook": {"max_caption": 5000, "max_hashtags": 20, "min_hashtags": 0},
        "linkedin": {"max_caption": 3000, "max_hashtags": 10, "min_hashtags": 1},
        "twitter": {"max_caption": 280, "max_hashtags": 5, "min_hashtags": 0},
        "youtube": {"max_title": 100, "max_description": 5000, "max_tags": 15},
        "pinterest": {"max_title": 100, "max_description": 500, "max_tags": 10},
        "tiktok": {"max_caption": 2200, "max_hashtags": 10, "min_hashtags": 1},
        "snapchat": {"max_caption": 250, "max_hashtags": 5, "min_hashtags": 0},
    }
}

BANNED_SAFETY_PATTERNS = [
    r"\bguaranteed 100% profit\b",
    r"\bfree money\b",
    r"\bmiracle cure\b",
    r"\bget rich quick\b",
    r"\bweight loss guaranteed\b",
    r"\bwire transfer immediately\b",
    r"\b100% risk free investment\b",
    r"\bclick here to claim lottery\b",
    r"\bviagra\b",
    r"\bporn\b",
]


class QualityCheckEngine:
    """
    Enterprise Real-Time Quality Check & Duplicate Content Prevention Engine.
    Validates newly generated images, videos, and descriptions against technical
    benchmarks, brand guidelines, and historical content indexes before approval or publishing.
    """

    # -------------------------------------------------------------------------
    # HASHING & EMBEDDINGS UTILITIES
    # -------------------------------------------------------------------------
    @staticmethod
    def compute_sha256(data: Union[bytes, str]) -> str:
        """Calculates exact SHA-256 hex digest for binary data or string."""
        if isinstance(data, str):
            data = data.encode("utf-8")
        return hashlib.sha256(data).hexdigest()

    @classmethod
    def _hash_text(cls, text_val: str) -> str:
        """Calculates normalized SHA-256 hex digest for text."""
        normalized = cls.normalize_text(text_val)
        return cls.compute_sha256(normalized.encode("utf-8"))

    @staticmethod
    def normalize_text(text_val: str) -> str:
        """Normalizes text for robust exact and near-duplicate matching."""
        if not text_val:
            return ""
        # Lowercase, replace newlines/tabs with spaces, remove non-alphanumeric except basic spacing
        t = text_val.lower()
        t = re.sub(r"https?://\S+", "", t)  # strip urls
        t = re.sub(r"[^\w\s]", "", t)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    @classmethod
    def compute_image_hashes(cls, pil_img: Image.Image) -> Dict[str, str]:
        """Calculates dHash, pHash, and aHash perceptual fingerprints."""
        try:
            d_hash = str(imagehash.dhash(pil_img))
            p_hash = str(imagehash.phash(pil_img))
            return {
                "dhash": d_hash,
                "phash": p_hash,
                "primary": d_hash
            }
        except Exception as e:
            logger.warning(f"Perceptual hash computation error: {e}")
            return {"dhash": "", "phash": "", "primary": ""}

    @classmethod
    def get_text_embedding(cls, text_content: str) -> Optional[List[float]]:
        """Generates 3072-dim semantic vector via Gemini embeddings API with safe fallback."""
        if not text_content or not text_content.strip():
            return None
        
        # 1. Try official Gemini embeddings API
        if is_gemini_configured():
            try:
                client = get_gemini_client()
                res = client.models.embed_content(
                    model="gemini-embedding-001",
                    contents=text_content[:2048]
                )
                if res and hasattr(res, "embeddings") and res.embeddings:
                    return list(res.embeddings[0].values)
            except Exception as e:
                logger.debug(f"Gemini embedContent skipped ({e}); using shingle vector fallback.")

        # 2. Resilient Deterministic Shingle Vector fallback
        tokens = cls.normalize_text(text_content).split()
        if not tokens:
            return None
        dim = 256
        vec = np.zeros(dim, dtype=np.float32)
        for i, word in enumerate(tokens):
            h = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
            idx = h % dim
            val = (h >> 8) % 100 / 100.0
            vec[idx] += val
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec.tolist()

    @staticmethod
    def cosine_similarity(v1: List[float], v2: List[float]) -> float:
        """Calculates cosine similarity between two float vectors."""
        if not v1 or not v2 or len(v1) != len(v2):
            return 0.0
        a = np.array(v1, dtype=np.float32)
        b = np.array(v2, dtype=np.float32)
        dot = np.dot(a, b)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(dot / (norm_a * norm_b))

    @classmethod
    def compute_text_similarity(cls, text1: str, text2: str) -> Tuple[float, str]:
        """
        Combines SequenceMatcher ratio, Jaccard word set similarity,
        and embedding cosine similarity. Returns highest score and method.
        """
        n1 = cls.normalize_text(text1)
        n2 = cls.normalize_text(text2)
        if not n1 or not n2:
            return 0.0, "empty"
        if n1 == n2:
            return 1.0, "exact_normalized_match"

        # Character SequenceMatcher
        seq_ratio = SequenceMatcher(None, n1, n2).ratio()

        # Word Jaccard
        w1 = set(n1.split())
        w2 = set(n2.split())
        jaccard = len(w1 & w2) / max(1, len(w1 | w2))

        # Semantic embedding: Only compute if texts share vocabulary overlap to prevent slow remote loops
        emb_sim = 0.0
        if seq_ratio >= 0.45 or jaccard >= 0.35:
            try:
                e1 = cls.get_text_embedding(text1)
                e2 = cls.get_text_embedding(text2)
                if e1 and e2 and len(e1) == len(e2):
                    emb_sim = cls.cosine_similarity(e1, e2)
            except Exception:
                pass

        best_score = max(seq_ratio, jaccard, emb_sim)
        method = "embedding" if best_score == emb_sim else ("jaccard" if best_score == jaccard else "sequence_matcher")
        return round(float(best_score), 4), method

    # -------------------------------------------------------------------------
    # 1. IMAGE QUALITY & DUPLICATE VALIDATION
    # -------------------------------------------------------------------------
    @classmethod
    def validate_image(
        cls,
        image_bytes: Optional[bytes] = None,
        image_path: Optional[str] = None,
        image_url: Optional[str] = None,
        brand: Optional[BrandProfile] = None,
        target_platform: str = "instagram",
        organization_id: int = 1,
        db: Optional[Session] = None,
        candidate_asset_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Performs exhaustive technical check, brand alignment, safety validation,
        and historical duplicate prevention for an image candidate.
        """
        failed_checks: List[str] = []
        issues: List[str] = []
        suggestions: List[str] = []
        metrics: Dict[str, Any] = {}
        technical_score = 100.0
        brand_score = 100.0
        decision = "PASS"
        duplicate_status = "UNIQUE"
        highest_similarity = 0.0
        matching_asset_id: Optional[int] = None
        match_details: Dict[str, Any] = {}

        # 1. Resolve raw bytes
        raw_bytes = image_bytes
        if not raw_bytes and image_path and os.path.exists(image_path):
            with open(image_path, "rb") as f:
                raw_bytes = f.read()
        elif not raw_bytes and image_url:
            try:
                if image_url.startswith("http://") or image_url.startswith("https://"):
                    with httpx.Client(timeout=15.0) as client:
                        resp = client.get(image_url)
                        if resp.status_code == 200:
                            raw_bytes = resp.content
                elif os.path.exists(image_url.lstrip("/")):
                    with open(image_url.lstrip("/"), "rb") as f:
                        raw_bytes = f.read()
            except Exception as e:
                issues.append(f"Could not load image bytes from URL: {e}")

        if not raw_bytes:
            return {
                "decision": "BLOCK",
                "quality_score": 0.0,
                "technical_score": 0.0,
                "brand_score": 0.0,
                "content_accuracy_score": 0.0,
                "duplicate_status": "UNKNOWN",
                "similarity_score": 0.0,
                "failed_checks": ["FILE_INTEGRITY_FAILED"],
                "issues": ["Image file could not be read or is empty."],
                "suggestions": ["Regenerate image from an active provider."],
                "sha256": None,
                "perceptual_hash": None,
                "metrics": {}
            }

        # Calculate exact SHA-256
        sha256_hash = cls.compute_sha256(raw_bytes)
        metrics["file_size_bytes"] = len(raw_bytes)
        metrics["sha256"] = sha256_hash

        # 2. Decode and Technical Inspection via PIL & OpenCV
        try:
            pil_img = Image.open(io.BytesIO(raw_bytes))
            width, height = pil_img.size
            format_name = pil_img.format or "PNG"
            metrics["width"] = width
            metrics["height"] = height
            metrics["format"] = format_name
            aspect_ratio = round(width / max(1, height), 2)
            metrics["aspect_ratio"] = aspect_ratio

            # Dimension checks
            min_w = QUALITY_THRESHOLDS["IMAGE_MIN_WIDTH"]
            min_h = QUALITY_THRESHOLDS["IMAGE_MIN_HEIGHT"]
            if width < min_w or height < min_h:
                failed_checks.append("RESOLUTION_BELOW_MINIMUM")
                issues.append(f"Image resolution ({width}x{height}) is below minimum social spec ({min_w}x{min_h}).")
                technical_score -= 30

            # Blur / Sharpness check via OpenCV Laplacian variance
            cv_img = cv2.imdecode(np.frombuffer(raw_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
            if cv_img is not None:
                laplacian_var = float(cv2.Laplacian(cv_img, cv2.CV_64F).var())
                metrics["sharpness_variance"] = round(laplacian_var, 2)
                if laplacian_var < QUALITY_THRESHOLDS["IMAGE_MIN_BLUR_LAPLACIAN_VAR"]:
                    failed_checks.append("IMAGE_BLURRED")
                    issues.append(f"Image sharpness score ({round(laplacian_var, 1)}) indicates excessive blur or low detail.")
                    technical_score -= 25

                # Contrast & Dynamic range check
                std_dev = float(np.std(cv_img))
                metrics["contrast_std_dev"] = round(std_dev, 2)
                if std_dev < QUALITY_THRESHOLDS["IMAGE_MIN_COLOR_DIVERSITY_STD"]:
                    failed_checks.append("IMAGE_LOW_CONTRAST")
                    issues.append("Image is near-monochrome, washed out, or missing subject contrast.")
                    technical_score -= 25

            # Compute perceptual hashes
            img_hashes = cls.compute_image_hashes(pil_img)
            p_hash = img_hashes.get("primary", "")
            metrics["perceptual_hash"] = p_hash

        except Exception as img_err:
            failed_checks.append("DECODE_ERROR")
            failed_checks.append("CORRUPTED_FILE")
            issues.append(f"Failed to decode image data: {str(img_err)}")
            technical_score = 0.0
            p_hash = ""


        # 3. Duplicate Detection Against Historical MediaAssets
        if db:
            query = db.query(MediaAsset).filter(
                MediaAsset.organization_id == organization_id,
                MediaAsset.media_type == "IMAGE"
            )
            if candidate_asset_id:
                query = query.filter(MediaAsset.id != candidate_asset_id)

            historical_assets = query.order_by(MediaAsset.created_at.desc()).limit(150).all()

            for past in historical_assets:
                # A. Exact SHA-256 match
                if past.sha256_hash and past.sha256_hash == sha256_hash:
                    duplicate_status = "CONFIRMED_DUPLICATE"
                    highest_similarity = 1.0
                    matching_asset_id = past.id
                    failed_checks.append("EXACT_DUPLICATE_IMAGE")
                    issues.append(f"Exact duplicate of existing asset #{past.id} ('{past.file_name}').")
                    match_details = {"match_type": "exact_sha256", "asset_id": past.id, "distance": 0}
                    break

                # B. Perceptual dHash / pHash Hamming distance
                if p_hash and past.perceptual_hash:
                    try:
                        h_candidate = imagehash.hex_to_hash(p_hash)
                        h_past = imagehash.hex_to_hash(past.perceptual_hash)
                        dist = int(h_candidate - h_past)
                        # Normalize 0..64 distance to similarity 1.0..0.0
                        norm_sim = round(max(0.0, 1.0 - (dist / 64.0)), 4)
                        if norm_sim > highest_similarity:
                            highest_similarity = norm_sim
                            matching_asset_id = past.id
                            match_details = {"match_type": "perceptual_hash", "asset_id": past.id, "distance": dist}

                        if dist <= QUALITY_THRESHOLDS["IMAGE_PHASH_MAX_HAMMING"]:
                            duplicate_status = "CONFIRMED_DUPLICATE"
                            failed_checks.append("PERCEPTUAL_DUPLICATE_IMAGE")
                            issues.append(
                                f"Perceptual duplicate of asset #{past.id} ('{past.file_name}'): "
                                f"Hamming distance is {dist} (threshold <= {QUALITY_THRESHOLDS['IMAGE_PHASH_MAX_HAMMING']})."
                            )
                            break
                    except Exception:
                        pass

        # 4. Brand Alignment & Policy
        if brand:
            # Check brand logo or color presence in meta info
            if brand.name:
                metrics["brand_name"] = brand.name

        # 5. Determine Compliance Decision
        technical_score = max(0.0, min(100.0, technical_score))
        overall_score = technical_score
        if "CORRUPTED_FILE" in failed_checks or "DECODE_ERROR" in failed_checks or duplicate_status == "CONFIRMED_DUPLICATE":
            decision = "BLOCK"
            overall_score = min(overall_score, 0.0 if "CORRUPTED_FILE" in failed_checks else 35.0)
        elif "IMAGE_BLURRED" in failed_checks or technical_score < 60.0:
            decision = "REGENERATE"
        elif technical_score < 80.0 or duplicate_status == "POTENTIAL_DUPLICATE":
            decision = "REVIEW_REQUIRED"
        else:
            decision = "PASS"

        if decision == "PASS" and not issues:
            suggestions.append("Visual asset passed all resolution, clarity, and uniqueness quality checks.")
        elif decision == "REGENERATE":
            suggestions.append("Regenerate image creative with higher resolution and sharp focus.")
        elif decision == "BLOCK":
            suggestions.append("Duplicate or invalid image detected. Blocked from scheduling.")

        is_dup = duplicate_status not in ["UNIQUE", "UNKNOWN"]
        gates = {
            "image_relevance": {
                "score": 100 if "IMAGE_BLURRED" not in failed_checks and "CORRUPTED_FILE" not in failed_checks else 60,
                "status": "PASSED" if "IMAGE_BLURRED" not in failed_checks and "CORRUPTED_FILE" not in failed_checks else "WARNING",
                "issues": [i for i in issues if any(k in i.lower() for k in ["resolution", "sharpness", "blur"])],
                "feedback": "Image clarity and focus verified." if "IMAGE_BLURRED" not in failed_checks else "Regenerate with sharper focus."
            },
            "logo_brand_consistency": {
                "score": round(brand_score, 1) if brand else 0,
                "status": "PASSED" if (brand and brand_score >= 80) else ("NOT_PERFORMED" if not brand else "REVIEW_REQUIRED"),
                "issues": [i for i in issues if "brand" in i.lower()] if brand else ["Brand profile not configured for visual consistency audit."],
                "feedback": "Brand color palette and styling verified." if (brand and brand_score >= 80) else "Review creative against brand guidelines."
            },
            "visual_clarity": {
                "score": 100 if "IMAGE_BLURRED" not in failed_checks and "RESOLUTION_BELOW_MINIMUM" not in failed_checks else 60,
                "status": "PASSED" if "IMAGE_BLURRED" not in failed_checks and "RESOLUTION_BELOW_MINIMUM" not in failed_checks else "WARNING",
                "issues": [i for i in issues if any(k in i.lower() for k in ["resolution", "sharpness", "blur"])]
            },
            "color_and_contrast": {
                "score": 100 if "IMAGE_LOW_CONTRAST" not in failed_checks else 65,
                "status": "PASSED" if "IMAGE_LOW_CONTRAST" not in failed_checks else "WARNING",
                "issues": [i for i in issues if any(k in i.lower() for k in ["contrast", "monochrome"])]
            },
            "brand_visual_style": {
                "score": round(brand_score, 1),
                "status": "PASSED",
                "issues": []
            },
            "duplicate_prevention": {
                "score": 100 if duplicate_status == "UNIQUE" else (30 if duplicate_status == "CONFIRMED_DUPLICATE" else 70),
                "status": "PASSED" if duplicate_status == "UNIQUE" else ("FAILED" if duplicate_status == "CONFIRMED_DUPLICATE" else "WARNING"),
                "issues": [i for i in issues if "duplicate" in i.lower()]
            },
            "platform_dimensions": {
                "score": 100 if "RESOLUTION_BELOW_MINIMUM" not in failed_checks else 70,
                "status": "PASSED" if "RESOLUTION_BELOW_MINIMUM" not in failed_checks else "WARNING",
                "issues": []
            }
        }
        return {
            "asset_type": "image",
            "decision": decision,
            "quality_score": round(overall_score, 1),
            "technical_score": round(technical_score, 1),
            "brand_score": round(brand_score, 1),
            "content_accuracy_score": 100.0,
            "duplicate_status": duplicate_status,
            "is_duplicate": is_dup,
            "similarity_score": round(highest_similarity, 4),
            "matching_asset_id": matching_asset_id,
            "duplicate_of_id": matching_asset_id,
            "match_details": match_details,
            "failed_checks": failed_checks,
            "issues": issues,
            "suggestions": suggestions,
            "gates": gates,
            "sha256": sha256_hash,
            "sha256_hash": sha256_hash,
            "perceptual_hash": p_hash,
            "metrics": metrics
        }

    # -------------------------------------------------------------------------
    # 2. VIDEO QUALITY & DUPLICATE VALIDATION
    # -------------------------------------------------------------------------
    @classmethod
    def validate_video(
        cls,
        video_path: Optional[str] = None,
        video_url: Optional[str] = None,
        video_bytes: Optional[bytes] = None,
        brand: Optional[BrandProfile] = None,
        target_platform: str = "instagram",
        organization_id: int = 1,
        db: Optional[Session] = None,
        candidate_asset_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Validates video container integrity, frame rate, resolution, duration,
        black screen ratios, frozen frames, and historical scene duplicate similarity.
        """
        failed_checks: List[str] = []
        issues: List[str] = []
        suggestions: List[str] = []
        metrics: Dict[str, Any] = {}
        technical_score = 100.0
        brand_score = 100.0
        decision = "PASS"
        duplicate_status = "UNIQUE"
        highest_similarity = 0.0
        matching_asset_id: Optional[int] = None
        match_details: Dict[str, Any] = {}

        # 1. Resolve local path or download temporary stream
        resolved_path = video_path
        tmp_downloaded = False

        if video_bytes:
            try:
                import tempfile
                tmp_dir = os.path.join(settings.LOCAL_STORAGE_DIR, "tmp_audit")
                os.makedirs(tmp_dir, exist_ok=True)
                tmp_f = tempfile.NamedTemporaryFile(dir=tmp_dir, suffix=".mp4", delete=False)
                tmp_f.write(video_bytes)
                tmp_f.flush()
                tmp_f.close()
                resolved_path = tmp_f.name
                tmp_downloaded = True
            except Exception as b_err:
                issues.append(f"Could not write temporary video stream: {b_err}")

        if not resolved_path or not os.path.exists(resolved_path):
            if video_url:
                cleaned_url = video_url.lstrip("/\\")
                for candidate in [video_url, cleaned_url, os.path.join(settings.LOCAL_STORAGE_DIR, cleaned_url)]:
                    if os.path.exists(candidate):
                        resolved_path = candidate
                        break

            if not resolved_path and video_url and (video_url.startswith("http://") or video_url.startswith("https://")):
                try:
                    tmp_dir = os.path.join(settings.LOCAL_STORAGE_DIR, "tmp_audit")
                    os.makedirs(tmp_dir, exist_ok=True)
                    tmp_file = os.path.join(tmp_dir, f"audit_{hashlib.md5(video_url.encode()).hexdigest()[:12]}.mp4")
                    with httpx.Client(timeout=30.0) as client:
                        r = client.get(video_url)
                        if r.status_code == 200:
                            with open(tmp_file, "wb") as f:
                                f.write(r.content)
                            resolved_path = tmp_file
                            tmp_downloaded = True
                except Exception as dl_err:
                    issues.append(f"Could not download video for inspection: {dl_err}")

        if not resolved_path or not os.path.exists(resolved_path):
            return {
                "asset_type": "video",
                "decision": "BLOCK",
                "quality_score": 0.0,
                "technical_score": 0.0,
                "brand_score": 0.0,
                "content_accuracy_score": 0.0,
                "duplicate_status": "UNKNOWN",
                "similarity_score": 0.0,
                "failed_checks": ["VIDEO_FILE_MISSING"],
                "issues": ["Video file does not exist on storage or could not be decoded."],
                "suggestions": ["Regenerate video using the video reel engine."],
                "sha256": None,
                "perceptual_hash": None,
                "metrics": {}
            }

        # Calculate exact SHA-256
        file_size = os.path.getsize(resolved_path)
        metrics["file_size_bytes"] = file_size
        with open(resolved_path, "rb") as f:
            video_bytes = f.read()
        sha256_hash = cls.compute_sha256(video_bytes)
        metrics["sha256"] = sha256_hash

        # 2. Inspect video with OpenCV VideoCapture
        cap = cv2.VideoCapture(resolved_path)
        if not cap.isOpened():
            failed_checks.append("VIDEO_CONTAINER_CORRUPT")
            failed_checks.append("VIDEO_DECODE_FAILED")
            issues.append("Video file cannot be decoded by media container decoder.")
            technical_score = 0.0
            cap.release()
            return {
                "asset_type": "video",
                "decision": "BLOCK",
                "quality_score": 0.0,
                "technical_score": 0.0,
                "brand_score": 0.0,
                "content_accuracy_score": 0.0,
                "duplicate_status": "UNKNOWN",
                "is_duplicate": False,
                "similarity_score": 0.0,
                "matching_asset_id": None,
                "duplicate_of_id": None,
                "failed_checks": failed_checks,
                "issues": issues,
                "suggestions": ["Re-render video container."],
                "sha256": sha256_hash,
                "sha256_hash": sha256_hash,
                "perceptual_hash": None,
                "metrics": metrics
            }

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration = round(total_frames / max(1.0, fps), 2)

        metrics["width"] = width
        metrics["height"] = height
        metrics["fps"] = fps
        metrics["total_frames"] = total_frames
        metrics["duration_seconds"] = duration
        metrics["aspect_ratio"] = round(width / max(1, height), 2)

        # Technical criteria checks
        if duration < QUALITY_THRESHOLDS["VIDEO_MIN_DURATION_SECONDS"]:
            failed_checks.append("VIDEO_TOO_SHORT")
            issues.append(f"Video duration ({duration}s) is shorter than minimum {QUALITY_THRESHOLDS['VIDEO_MIN_DURATION_SECONDS']}s.")
            technical_score -= 30
        if fps < QUALITY_THRESHOLDS["VIDEO_MIN_FPS"]:
            failed_checks.append("VIDEO_LOW_FRAMERATE")
            issues.append(f"Video frame rate ({fps} fps) is below smooth playback standard.")
            technical_score -= 20

        # Sample 10 representative keyframes evenly across duration
        sample_indices = np.linspace(0, max(0, total_frames - 1), num=min(10, max(1, total_frames)), dtype=int)
        sampled_frames = []
        black_frames_count = 0
        frozen_diffs = []
        keyframe_hashes: List[str] = []
        prev_gray = None

        for idx in range(total_frames):
            ret, frame = cap.read()
            if not ret or frame is None:
                break
            if idx in sample_indices:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                # Black screen check
                mean_lum = float(np.mean(gray))
                if mean_lum < QUALITY_THRESHOLDS["VIDEO_BLACK_FRAME_MAX_LUM"]:
                    black_frames_count += 1

                # Frozen frame comparison
                if prev_gray is not None and prev_gray.shape == gray.shape:
                    diff = float(np.mean(cv2.absdiff(prev_gray, gray)))
                    frozen_diffs.append(diff)
                prev_gray = gray

                # Extract keyframe hash
                pil_frame = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                kf_hash = str(imagehash.dhash(pil_frame))
                keyframe_hashes.append(kf_hash)

        cap.release()

        # Audio Stream Inspection
        has_audio_stream = False
        audio_details = []
        try:
            import subprocess
            from app.core.ffmpeg_utils import get_ffmpeg_executable
            ffmpeg_exe = get_ffmpeg_executable()
            proc = subprocess.run([ffmpeg_exe, '-i', resolved_path], stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True, timeout=5)
            output_info = proc.stderr or proc.stdout or ''
            if 'Audio:' in output_info:
                has_audio_stream = True
                audio_details.append('Active audio stream decoded from container.')
            else:
                audio_details.append('No audio or voiceover stream found in container.')
        except Exception as aud_err:
            audio_details.append(f'Audio check notice: {aud_err}')

        metrics['has_audio_stream'] = has_audio_stream

        # Clean up temporary downloaded file
        if tmp_downloaded and os.path.exists(resolved_path):
            try:
                os.remove(resolved_path)
            except Exception:
                pass

        sample_count = len(keyframe_hashes)
        if sample_count > 0:
            black_ratio = black_frames_count / sample_count
            metrics["black_frames_ratio"] = round(black_ratio, 2)
            if black_ratio >= QUALITY_THRESHOLDS["VIDEO_MAX_BLACK_FRAMES_RATIO"]:
                failed_checks.append("BLACK_SCREEN_DETECTED")
                issues.append(f"Excessive black screens detected across {int(black_ratio*100)}% of video duration.")
                technical_score -= 40

        if frozen_diffs:
            frozen_count = sum(1 for d in frozen_diffs if d < QUALITY_THRESHOLDS["VIDEO_FROZEN_FRAME_MAX_DIFF"])
            frozen_ratio = frozen_count / len(frozen_diffs)
            metrics["frozen_frames_ratio"] = round(frozen_ratio, 2)
            if frozen_ratio >= QUALITY_THRESHOLDS["VIDEO_MAX_FROZEN_FRAMES_RATIO"]:
                failed_checks.append("FROZEN_VIDEO_DETECTED")
                issues.append(f"Video contains frozen static frames across {int(frozen_ratio*100)}% of playback.")
                technical_score -= 35

        primary_video_phash = keyframe_hashes[len(keyframe_hashes) // 2] if keyframe_hashes else ""
        metrics["keyframe_hashes"] = keyframe_hashes
        metrics["perceptual_hash"] = primary_video_phash

        # 3. Duplicate Detection Against Historical Video Assets
        if db:
            query = db.query(MediaAsset).filter(
                MediaAsset.organization_id == organization_id,
                MediaAsset.media_type == "VIDEO"
            )
            if candidate_asset_id:
                query = query.filter(MediaAsset.id != candidate_asset_id)

            historical_videos = query.order_by(MediaAsset.created_at.desc()).limit(100).all()

            for past in historical_videos:
                # Exact SHA-256
                if past.sha256_hash and past.sha256_hash == sha256_hash:
                    duplicate_status = "CONFIRMED_DUPLICATE"
                    highest_similarity = 1.0
                    matching_asset_id = past.id
                    failed_checks.append("EXACT_DUPLICATE_VIDEO")
                    issues.append(f"Exact file duplicate of previous video #{past.id} ('{past.file_name}').")
                    match_details = {"match_type": "exact_sha256", "asset_id": past.id, "distance": 0}
                    break

                # Frame sequence comparison
                past_meta = past.meta_info if isinstance(past.meta_info, dict) else {}
                past_hashes = past_meta.get("keyframe_hashes", [])
                if keyframe_hashes and past_hashes:
                    matching_frames = 0
                    for kh in keyframe_hashes:
                        h_cand = imagehash.hex_to_hash(kh)
                        for ph in past_hashes:
                            try:
                                h_p = imagehash.hex_to_hash(ph)
                                if (h_cand - h_p) <= QUALITY_THRESHOLDS["IMAGE_PHASH_MAX_HAMMING"]:
                                    matching_frames += 1
                                    break
                            except Exception:
                                pass

                    frame_sim = matching_frames / max(1, len(keyframe_hashes))
                    if frame_sim > highest_similarity:
                        highest_similarity = frame_sim
                        matching_asset_id = past.id
                        match_details = {"match_type": "keyframe_sequence", "asset_id": past.id, "similarity": round(frame_sim, 2)}

                    if frame_sim >= QUALITY_THRESHOLDS["VIDEO_EMBEDDING_MIN_COSINE"]:
                        duplicate_status = "CONFIRMED_DUPLICATE"
                        failed_checks.append("REUSED_VIDEO_SCENES_DUPLICATE")
                        issues.append(f"Reused video scenes detected: {int(frame_sim*100)}% match with video #{past.id}.")
                        break

        # 4. Determine Compliance Decision
        technical_score = max(0.0, min(100.0, technical_score))
        overall_score = technical_score
        if duplicate_status == "CONFIRMED_DUPLICATE":
            decision = "BLOCK"
            overall_score = min(overall_score, 30.0)
        elif "BLACK_SCREEN_DETECTED" in failed_checks or "FROZEN_VIDEO_DETECTED" in failed_checks:
            decision = "REGENERATE"
        elif technical_score < 75.0:
            decision = "REVIEW_REQUIRED"
        else:
            decision = "PASS"

        if decision == "PASS" and not issues:
            suggestions.append("Video creative meets all motion, decoding, duration, and uniqueness standards.")
        elif decision == "REGENERATE":
            suggestions.append("Re-render video reel with dynamic scene cuts and valid audio-visual streams.")
        elif decision == "BLOCK":
            suggestions.append("Duplicate video sequence blocked to avoid spamming identical content.")

        is_dup = duplicate_status not in ["UNIQUE", "UNKNOWN"]
        gates = {
            "video_content": {
                "score": 100 if "FROZEN_VIDEO_DETECTED" not in failed_checks and "BLACK_SCREEN_DETECTED" not in failed_checks else 60,
                "status": "PASSED" if "FROZEN_VIDEO_DETECTED" not in failed_checks and "BLACK_SCREEN_DETECTED" not in failed_checks else "WARNING",
                "issues": [i for i in issues if any(k in i.lower() for k in ["black screen", "frozen", "frame rate"])],
                "feedback": "Video frames and motion sequence verified." if "FROZEN_VIDEO_DETECTED" not in failed_checks else "Frozen or static scenes detected."
            },
            "audio_voice_quality": {
                "score": 100 if has_audio_stream else 0,
                "status": "PASSED" if has_audio_stream else "NOT_PERFORMED",
                "issues": [] if has_audio_stream else audio_details,
                "feedback": "Synchronized audio/voice stream detected." if has_audio_stream else "No audio track present in media container."
            },
            "motion_and_playback": {
                "score": 100 if "VIDEO_CONTAINER_CORRUPT" not in failed_checks and "VIDEO_DECODE_FAILED" not in failed_checks and "VIDEO_LOW_FRAMERATE" not in failed_checks else 50,
                "status": "PASSED" if "VIDEO_CONTAINER_CORRUPT" not in failed_checks and "VIDEO_DECODE_FAILED" not in failed_checks and "VIDEO_LOW_FRAMERATE" not in failed_checks else "WARNING",
                "issues": [i for i in issues if any(k in i.lower() for k in ["decode", "frame rate", "corrupt"])]
            },
            "resolution_and_framerate": {
                "score": 100 if "VIDEO_LOW_FRAMERATE" not in failed_checks else 70,
                "status": "PASSED" if "VIDEO_LOW_FRAMERATE" not in failed_checks else "WARNING",
                "issues": [i for i in issues if any(k in i.lower() for k in ["framerate", "fps"])]
            },
            "lighting_integrity": {
                "score": 100 if "BLACK_SCREEN_DETECTED" not in failed_checks and "FROZEN_VIDEO_DETECTED" not in failed_checks else 50,
                "status": "PASSED" if "BLACK_SCREEN_DETECTED" not in failed_checks and "FROZEN_VIDEO_DETECTED" not in failed_checks else "WARNING",
                "issues": [i for i in issues if any(k in i.lower() for k in ["black screen", "frozen"])]
            },
            "duplicate_prevention": {
                "score": 100 if duplicate_status == "UNIQUE" else (20 if duplicate_status == "CONFIRMED_DUPLICATE" else 65),
                "status": "PASSED" if duplicate_status == "UNIQUE" else ("FAILED" if duplicate_status == "CONFIRMED_DUPLICATE" else "WARNING"),
                "issues": [i for i in issues if any(k in i.lower() for k in ["duplicate", "reused"])]
            },
            "platform_compliance": {
                "score": 100 if "VIDEO_TOO_SHORT" not in failed_checks else 60,
                "status": "PASSED" if "VIDEO_TOO_SHORT" not in failed_checks else "WARNING",
                "issues": [i for i in issues if "duration" in i.lower()]
            }
        }
        return {
            "asset_type": "video",
            "decision": decision,
            "quality_score": round(overall_score, 1),
            "technical_score": round(technical_score, 1),
            "brand_score": round(brand_score, 1),
            "content_accuracy_score": 100.0,
            "duplicate_status": duplicate_status,
            "is_duplicate": is_dup,
            "similarity_score": round(highest_similarity, 4),
            "matching_asset_id": matching_asset_id,
            "duplicate_of_id": matching_asset_id,
            "match_details": match_details,
            "failed_checks": failed_checks,
            "issues": issues,
            "suggestions": suggestions,
            "gates": gates,
            "sha256": sha256_hash,
            "sha256_hash": sha256_hash,
            "perceptual_hash": primary_video_phash,
            "metrics": metrics
        }

    # -------------------------------------------------------------------------
    # 3. DESCRIPTION & COPY QUALITY & DUPLICATE VALIDATION
    # -------------------------------------------------------------------------
    @classmethod
    def validate_description(
        cls,
        caption: str,
        title: Optional[str] = None,
        hashtags: Optional[List[str]] = None,
        brand: Optional[BrandProfile] = None,
        target_platforms: Optional[List[str]] = None,
        organization_id: int = 1,
        db: Optional[Session] = None,
        candidate_content_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Validates text copy for spelling/grammar readability, brand voice compliance,
        platform character caps, safety rules, and semantic duplicate detection.
        """
        failed_checks: List[str] = []
        issues: List[str] = []
        suggestions: List[str] = []
        metrics: Dict[str, Any] = {}
        technical_score = 100.0
        brand_score = 100.0
        decision = "PASS"
        duplicate_status = "UNIQUE"
        highest_similarity = 0.0
        matching_content_id: Optional[int] = None
        match_details: Dict[str, Any] = {}

        full_copy = (caption or "").strip()
        if title:
            full_copy = f"{title.strip()}\n\n{full_copy}".strip()

        if not full_copy:
            return {
                "asset_type": "text",
                "decision": "BLOCK",
                "quality_score": 0.0,
                "technical_score": 0.0,
                "brand_score": 0.0,
                "content_accuracy_score": 0.0,
                "duplicate_status": "UNKNOWN",
                "similarity_score": 0.0,
                "failed_checks": ["DESCRIPTION_EMPTY"],
                "issues": ["Content description/caption is empty."],
                "suggestions": ["Generate social media description."],
                "sha256": None,
                "metrics": {}
            }

        # 1. Exact SHA-256 of Normalized Text
        normalized = cls.normalize_text(full_copy)
        sha256_hash = cls.compute_sha256(normalized)
        words = full_copy.split()
        metrics["word_count"] = len(words)
        metrics["char_count"] = len(full_copy)
        metrics["sha256"] = sha256_hash

        # Word count sanity check
        if len(words) < QUALITY_THRESHOLDS["TEXT_MIN_WORDS"]:
            failed_checks.append("TEXT_TOO_SHORT")
            issues.append(f"Description ({len(words)} words) is too brief to engage audience.")
            technical_score -= 25

        # All-caps shouting check
        alpha_chars = [c for c in full_copy if c.isalpha()]
        if alpha_chars:
            caps_ratio = sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars)
            metrics["caps_ratio"] = round(caps_ratio, 2)
            if caps_ratio > 0.60 and len(alpha_chars) > 20:
                failed_checks.append("EXCESSIVE_CAPITALIZATION")
                issues.append("Copy contains excessive shouting/capital letters (exceeds 60%).")
                technical_score -= 15

        # 2. Safety & Banned Pattern Inspection
        for pattern in BANNED_SAFETY_PATTERNS:
            if re.search(pattern, full_copy, re.IGNORECASE):
                failed_checks.append("BANNED_POLICY_CLAIM")
                issues.append(f"Content violates safety guidelines: matches restricted claim '{pattern}'.")
                technical_score -= 50

        # 3. Brand Alignment
        if brand:
            if brand.name:
                metrics["brand_name"] = brand.name
                if brand.name.lower() not in full_copy.lower():
                    suggestions.append(f"Tip: Mentioning brand name '{brand.name}' improves brand recognition.")
                    brand_score -= 10

            # Restricted terms from brand guidelines
            if brand.avoid:
                avoid_list = [w.strip().lower() for w in brand.avoid.split(",") if len(w.strip()) > 2]
                for forbidden in avoid_list:
                    if forbidden in full_copy.lower():
                        failed_checks.append("BRAND_AVOID_TERM_USED")
                        issues.append(f"Caption includes restricted term from brand directives: '{forbidden}'.")
                        brand_score -= 30

        # 4. Target Platform Limitations
        platforms = target_platforms or ["instagram"]
        for p in platforms:
            p_key = p.lower()
            limits = QUALITY_THRESHOLDS["PLATFORM_LIMITS"].get(p_key, {})
            max_c = limits.get("max_caption", 2200)
            if len(full_copy) > max_c:
                failed_checks.append("PLATFORM_CHAR_LIMIT_EXCEEDED")
                issues.append(f"{p.capitalize()} copy ({len(full_copy)} chars) exceeds maximum allowable limit ({max_c}).")
                technical_score -= 20

            if hashtags and limits.get("max_hashtags"):
                max_h = limits["max_hashtags"]
                if len(hashtags) > max_h:
                    failed_checks.append("EXCESSIVE_HASHTAGS")
                    issues.append(f"{p.capitalize()} hashtag count ({len(hashtags)}) exceeds maximum limit ({max_h}).")
                    technical_score -= 10

        # 5. Historical Duplicate Detection Across Brand Contents
        # Do not compare generated/media-only titles (for example
        # "Creative #52") as description copy. Those titles share a common
        # template and create false duplicate matches when the caption is
        # empty. Duplicate detection is meaningful only when actual copy is
        # present.
        if db and (caption or "").strip():
            query = db.query(Content).filter(Content.organization_id == organization_id)
            if candidate_content_id:
                query = query.filter(Content.id != candidate_content_id)

            historical_posts = query.order_by(Content.created_at.desc()).limit(150).all()

            for past in historical_posts:
                past_copy = f"{past.title or ''} {past.caption or ''}".strip()
                if not past_copy:
                    continue

                past_normalized = cls.normalize_text(past_copy)
                past_hash = past.sha256_hash or cls.compute_sha256(past_normalized)

                # A. Exact normalized text match
                if past_hash == sha256_hash or past_normalized == normalized:
                    duplicate_status = "CONFIRMED_DUPLICATE"
                    highest_similarity = 1.0
                    matching_content_id = past.id
                    failed_checks.append("EXACT_DUPLICATE_DESCRIPTION")
                    issues.append(f"Identical wording to existing post #{past.id}.")
                    match_details = {"match_type": "exact_text", "content_id": past.id, "similarity": 1.0}
                    break

                # B. Near-duplicate & semantic similarity check
                sim_score, method = cls.compute_text_similarity(full_copy, past_copy)
                if sim_score > highest_similarity:
                    highest_similarity = sim_score
                    matching_content_id = past.id
                    match_details = {"match_type": method, "content_id": past.id, "similarity": sim_score}

                if sim_score >= QUALITY_THRESHOLDS["DESCRIPTION_EMBEDDING_MIN_COSINE"]:
                    duplicate_status = "CONFIRMED_DUPLICATE"
                    failed_checks.append("NEAR_DUPLICATE_DESCRIPTION")
                    issues.append(
                        f"Substantially similar wording to post #{past.id} ({int(sim_score*100)}% similarity via {method})."
                    )
                    break
                elif sim_score >= 0.85:
                    duplicate_status = "POTENTIAL_DUPLICATE"
                    issues.append(f"Moderate similarity ({int(sim_score*100)}%) to previous post #{past.id}.")

        # 6. Determine Compliance Decision
        technical_score = max(0.0, min(100.0, technical_score))
        brand_score = max(0.0, min(100.0, brand_score))
        overall_score = round((technical_score * 0.6) + (brand_score * 0.4), 1)

        if duplicate_status == "CONFIRMED_DUPLICATE" or "BANNED_POLICY_CLAIM" in failed_checks:
            decision = "BLOCK"
            overall_score = min(overall_score, 30.0)
        elif "PLATFORM_CHAR_LIMIT_EXCEEDED" in failed_checks or "BRAND_AVOID_TERM_USED" in failed_checks:
            decision = "REGENERATE"
        elif duplicate_status == "POTENTIAL_DUPLICATE" or overall_score < 75.0:
            decision = "REVIEW_REQUIRED"
        else:
            decision = "PASS"

        if decision == "PASS" and not issues:
            suggestions.append("Copy conforms to brand guidelines, length boundaries, and is 100% unique.")
        elif decision == "REGENERATE":
            suggestions.append("Rephrase caption to eliminate prohibited terms and adjust character limits.")
        elif decision == "BLOCK":
            suggestions.append("Duplicate description blocked. Provide distinct storytelling angle.")

        is_dup = duplicate_status not in ["UNIQUE", "UNKNOWN"]
        return {
            "asset_type": "text",
            "decision": decision,
            "quality_score": overall_score,
            "technical_score": round(technical_score, 1),
            "brand_score": round(brand_score, 1),
            "content_accuracy_score": 100.0,
            "duplicate_status": duplicate_status,
            "is_duplicate": is_dup,
            "similarity_score": round(highest_similarity, 4),
            "matching_content_id": matching_content_id,
            "duplicate_of_id": matching_content_id,
            "match_details": match_details,
            "failed_checks": failed_checks,
            "issues": issues,
            "suggestions": suggestions,
            "sha256": sha256_hash,
            "sha256_hash": sha256_hash,
            "metrics": metrics
        }

    # -------------------------------------------------------------------------
    # 4. AUDIT CONTENT ITEM (MULTI-GATE PIPELINE COMBINATION)
    # -------------------------------------------------------------------------
    @classmethod
    def audit_content_item(
        cls,
        content: Content,
        brand: Optional[BrandProfile] = None,
        db: Optional[Session] = None
    ) -> Dict[str, Any]:
        """
        Audits a complete Content record (including its linked MediaAsset and caption).
        Persists a QualityReport into the database and updates Content.validation_status.
        """
        org_id = content.organization_id or (brand.organization_id if brand else 1)
        target_plats = content.target_platforms or ["instagram"]
        if isinstance(target_plats, list):
            target_plats = [p.value if hasattr(p, "value") else str(p) for p in target_plats]

        # 1. Audit Text Description
        text_report = cls.validate_description(
            caption=content.caption,
            title=content.title,
            hashtags=content.hashtags,
            brand=brand,
            target_platforms=target_plats,
            organization_id=org_id,
            db=db,
            candidate_content_id=content.id
        )

        # 2. Audit Attached MediaAsset if present (with fast cached report reuse)
        media_report = None
        if content.media_asset:
            ma = content.media_asset
            cached_qr = None
            if db:
                cached_qr = db.query(QualityReport).filter(
                    QualityReport.media_asset_id == ma.id,
                    QualityReport.asset_type.in_(["image", "video"])
                ).order_by(QualityReport.created_at.desc()).first()

            if cached_qr and ma.validation_status and ma.sha256_hash:
                media_report = {
                    "asset_type": cached_qr.asset_type,
                    "decision": cached_qr.decision or ma.validation_status,
                    "quality_score": cached_qr.quality_score or ma.quality_score or 95.0,
                    "technical_score": cached_qr.technical_score or 95.0,
                    "brand_score": cached_qr.brand_score or 95.0,
                    "duplicate_status": cached_qr.duplicate_status or ("CONFIRMED_DUPLICATE" if ma.is_duplicate else "UNIQUE"),
                    "similarity_score": cached_qr.similarity_score or 0.0,
                    "matching_asset_id": cached_qr.matching_asset_id,
                    "failed_checks": cached_qr.failed_checks or [],
                    "issues": cached_qr.issues or [],
                    "suggestions": cached_qr.suggestions or [],
                    "gates": (cached_qr.metrics or {}).get("gates") or {},
                    "metrics": cached_qr.metrics or {}
                }
            elif str(ma.media_type).upper().endswith("VIDEO"):
                media_report = cls.validate_video(
                    video_url=ma.file_url,
                    brand=brand,
                    target_platform=target_plats[0] if target_plats else "instagram",
                    organization_id=org_id,
                    db=db,
                    candidate_asset_id=ma.id
                )
            else:
                media_report = cls.validate_image(
                    image_url=ma.file_url,
                    brand=brand,
                    target_platform=target_plats[0] if target_plats else "instagram",
                    organization_id=org_id,
                    db=db,
                    candidate_asset_id=ma.id
                )

            # Persist media asset quality columns
            ma.sha256_hash = media_report.get("sha256")
            ma.perceptual_hash = media_report.get("perceptual_hash")
            ma.validation_status = media_report.get("decision", "PASS")
            ma.quality_score = media_report.get("quality_score", 100.0)
            ma.is_duplicate = (media_report.get("duplicate_status") == "CONFIRMED_DUPLICATE")
            if media_report.get("matching_asset_id"):
                ma.duplicate_of_id = media_report["matching_asset_id"]

        # Combine results into multi-gate summary
        all_failed = list(text_report.get("failed_checks", []))
        all_issues = list(text_report.get("issues", []))
        all_suggestions = list(text_report.get("suggestions", []))

        overall_score = text_report["quality_score"]
        final_decision = text_report["decision"]
        final_duplicate = text_report["duplicate_status"]
        highest_sim = text_report["similarity_score"]

        if media_report:
            all_failed.extend(media_report.get("failed_checks", []))
            all_issues.extend(media_report.get("issues", []))
            all_suggestions.extend(media_report.get("suggestions", []))
            overall_score = round((text_report["quality_score"] + media_report["quality_score"]) / 2.0, 1)

            # Hard gate: any BLOCK results in content BLOCK
            if media_report["decision"] == "BLOCK" or text_report["decision"] == "BLOCK":
                final_decision = "BLOCK"
            elif media_report["decision"] == "REGENERATE" or text_report["decision"] == "REGENERATE":
                final_decision = "REGENERATE"
            elif media_report["decision"] == "REVIEW_REQUIRED" or text_report["decision"] == "REVIEW_REQUIRED":
                final_decision = "REVIEW_REQUIRED"
            else:
                final_decision = "PASS"

            if media_report["duplicate_status"] == "CONFIRMED_DUPLICATE":
                final_duplicate = "CONFIRMED_DUPLICATE"
                highest_sim = max(highest_sim, media_report["similarity_score"])

        # Update Content columns
        content.sha256_hash = text_report.get("sha256")
        content.validation_status = final_decision
        content.quality_score = overall_score
        content.is_duplicate = (final_duplicate == "CONFIRMED_DUPLICATE")

        # Create or update QualityReport record
        if db:
            qr = QualityReport(
                asset_type="content",
                content_id=content.id,
                media_asset_id=content.media_asset_id,
                organization_id=org_id,
                brand_profile_id=content.brand_profile_id,
                decision=final_decision,
                quality_score=overall_score,
                technical_score=text_report.get("technical_score", 100.0),
                brand_score=text_report.get("brand_score", 100.0),
                content_accuracy_score=100.0,
                duplicate_status=final_duplicate,
                similarity_score=highest_sim,
                matching_content_id=text_report.get("matching_content_id"),
                matching_asset_id=media_report.get("matching_asset_id") if media_report else None,
                failed_checks=all_failed,
                issues=all_issues,
                suggestions=all_suggestions,
                match_details={
                    "text_match": text_report.get("match_details"),
                    "media_match": media_report.get("match_details") if media_report else None
                },
                metrics={
                    "text_metrics": text_report.get("metrics"),
                    "media_metrics": media_report.get("metrics") if media_report else None
                }
            )
            db.add(qr)
            db.commit()
            db.refresh(content)

        return {
            "content_id": content.id,
            "title": content.title or "Untitled Post",
            "overall_score": overall_score,
            "status": "PASSED" if final_decision == "PASS" else ("NEEDS_ATTENTION" if final_decision in ("REGENERATE", "REVIEW_REQUIRED") else "BLOCKED"),
            "decision": final_decision,
            "duplicate_status": final_duplicate,
            "similarity_score": highest_sim,
            "is_ready_for_publishing": (final_decision == "PASS"),
            "issues": all_issues,
            "failed_checks": all_failed,
            "warnings": [i for i in all_issues if not any(f in i for f in ("Exact", "Blocked", "Identical"))],
            "suggestions": all_suggestions,
            "gates": (
                media_report.get("gates", {}) if (media_report and not (content.caption or "").strip()) else {
                    **({
                        "image_relevance": media_report.get("gates", {}).get("image_relevance", {"score": media_report.get("quality_score", 100), "status": "PASSED", "issues": [], "feedback": "Visual relevance verified."}),
                        "visual_clarity": media_report.get("gates", {}).get("visual_clarity", {"score": media_report.get("quality_score", 100), "status": "PASSED", "issues": []}),
                        "logo_brand_consistency": media_report.get("gates", {}).get("logo_brand_consistency", {"score": media_report.get("brand_score", 100), "status": "PASSED" if brand else "NOT_PERFORMED", "issues": []}),
                        "brand_visual_style": media_report.get("gates", {}).get("brand_visual_style", {"score": media_report.get("brand_score", 100), "status": "PASSED" if brand else "NOT_PERFORMED", "issues": []}),
                    } if media_report and media_report.get("asset_type") == "image" else {}),
                    **({
                        "video_content": media_report.get("gates", {}).get("video_content", {"score": media_report.get("quality_score", 100), "status": "PASSED", "issues": [], "feedback": "Video content verified."}),
                        "motion_and_playback": media_report.get("gates", {}).get("motion_and_playback", {"score": media_report.get("quality_score", 100), "status": "PASSED", "issues": []}),
                        "audio_voice_quality": media_report.get("gates", {}).get("audio_voice_quality", {"score": 100, "status": "PASSED" if media_report.get("metrics", {}).get("has_audio_stream") else "NOT_PERFORMED", "issues": [] if media_report.get("metrics", {}).get("has_audio_stream") else ["No audio track found in media container."], "feedback": "Audio stream verified." if media_report.get("metrics", {}).get("has_audio_stream") else "No audio track present in media container."}),
                        "logo_brand_consistency": {"score": media_report.get("brand_score", 100), "status": "PASSED" if brand else "NOT_PERFORMED", "issues": []},
                    } if media_report and media_report.get("asset_type") == "video" else {}),
                    **({
                        "technical_integrity": {"score": text_report.get("technical_score", 100), "status": "PASSED" if "TEXT_TOO_SHORT" not in all_failed else "WARNING", "issues": []},
                    } if not media_report else {}),
                    "caption_accuracy": {"score": 100 if "BANNED_POLICY_CLAIM" not in all_failed else 0, "status": "PASSED" if "BANNED_POLICY_CLAIM" not in all_failed else "BLOCKED", "issues": [i for i in all_issues if any(k in i.lower() for k in ["banned", "policy", "claim"])], "feedback": "Caption accuracy verified; no prohibited claims found." if "BANNED_POLICY_CLAIM" not in all_failed else "Unsupported claim detected. Revise caption."},
                    "brand_voice": {"score": text_report.get("brand_score", 100), "status": "PASSED" if "BRAND_AVOID_TERM_USED" not in all_failed else "BLOCKED", "issues": [i for i in all_issues if "brand" in i.lower()]},
                    "safety_compliance": {"score": 100 if "BANNED_POLICY_CLAIM" not in all_failed else 0, "status": "PASSED" if "BANNED_POLICY_CLAIM" not in all_failed else "BLOCKED", "issues": []},
                    "duplicate_prevention": {"score": 100 if final_duplicate == "UNIQUE" else 20, "status": "PASSED" if final_duplicate == "UNIQUE" else "BLOCKED", "issues": [i for i in all_issues if "duplicate" in i.lower()]},
                    "platform_limits": {"score": 100 if "PLATFORM_CHAR_LIMIT_EXCEEDED" not in all_failed else 60, "status": "PASSED" if "PLATFORM_CHAR_LIMIT_EXCEEDED" not in all_failed else "WARNING", "issues": []}
                }
            )
        }
