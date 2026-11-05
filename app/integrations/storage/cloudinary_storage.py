import os
import uuid
import time
from typing import Dict, Any, Tuple
import cloudinary
import cloudinary.uploader
import cloudinary.utils
from fastapi import UploadFile, HTTPException, status
from app.core.config import settings
from app.core.logging import logger
from app.utils.enums import MediaType
from app.utils.validators import sanitize_filename

# Allowed MIME types and extensions
ALLOWED_IMAGE_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp"
}

ALLOWED_VIDEO_TYPES = {
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "video/webm": "webm"
}

ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm"}

MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024       # 10 MB
MAX_VIDEO_SIZE_BYTES = 100 * 1024 * 1024      # 100 MB


class CloudinaryStorageProvider:
    """Enterprise Cloudinary storage provider for images and videos."""

    @classmethod
    def is_configured(cls) -> bool:
        """Checks if valid Cloudinary credentials are configured."""
        return bool(
            settings.CLOUDINARY_CLOUD_NAME
            and settings.CLOUDINARY_API_KEY
            and settings.CLOUDINARY_API_SECRET
        )

    @classmethod
    def _init_cloudinary(cls):
        """Initializes Cloudinary SDK with settings credentials."""
        cloudinary.config(
            cloud_name=settings.CLOUDINARY_CLOUD_NAME,
            api_key=settings.CLOUDINARY_API_KEY,
            api_secret=settings.CLOUDINARY_API_SECRET,
            secure=True
        )

    @classmethod
    def validate_file(cls, filename: str, content_type: str, file_size: int) -> Tuple[MediaType, str]:
        """
        Validates format, extension, and file size.
        Returns (MediaType, extension).
        Raises HTTPException on invalid input.
        """
        if not filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File name cannot be empty."
            )

        ext = os.path.splitext(filename)[1].lower()
        mime = (content_type or "").lower().split(";")[0].strip()

        # Check image types
        if ext in ALLOWED_IMAGE_EXTENSIONS or mime in ALLOWED_IMAGE_TYPES:
            if ext not in ALLOWED_IMAGE_EXTENSIONS:
                ext = f".{ALLOWED_IMAGE_TYPES.get(mime, 'jpg')}"
            if file_size > MAX_IMAGE_SIZE_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Image file size exceeds the 10MB limit (provided {file_size / (1024 * 1024):.2f}MB)."
                )
            return MediaType.IMAGE, ext

        # Check video types
        if ext in ALLOWED_VIDEO_EXTENSIONS or mime in ALLOWED_VIDEO_TYPES:
            if ext not in ALLOWED_VIDEO_EXTENSIONS:
                ext = f".{ALLOWED_VIDEO_TYPES.get(mime, 'mp4')}"
            if file_size > MAX_VIDEO_SIZE_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Video file size exceeds the 100MB limit (provided {file_size / (1024 * 1024):.2f}MB)."
                )
            return MediaType.VIDEO, ext

        # If unsupported
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported file format '{ext}' ({content_type}). "
                f"Allowed image formats: PNG, JPG, JPEG, WebP. "
                f"Allowed video formats: MP4, MOV, WebM."
            )
        )

    @classmethod
    def upload_local_file_path(
        cls,
        file_path: str,
        organization_id: int = 1,
        folder_prefix: str = "socialai/ai_generated",
        resource_type: str = "image"
    ) -> Dict[str, Any]:
        """Upload an existing local file directly to Cloudinary and return durable metadata."""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        file_name = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)
        folder = f"{folder_prefix}/org_{organization_id}"

        # Automatically detect video vs image if default is used
        ext = os.path.splitext(file_name)[1].lower()
        if ext in {".mp4", ".mov", ".webm", ".avi", ".mkv"} or resource_type == "video":
            resource_type = "video"
        else:
            resource_type = "image"

        if cls.is_configured():
            try:
                cls._init_cloudinary()
                # Use chunked upload_large for videos to prevent size limits and timeouts on multi-MB reels
                if resource_type == "video":
                    upload_result = cloudinary.uploader.upload_large(
                        file_path,
                        folder=folder,
                        resource_type="video",
                        chunk_size=6000000,
                        use_filename=True,
                        unique_filename=True,
                        overwrite=False
                    )
                else:
                    upload_result = cloudinary.uploader.upload(
                        file_path,
                        folder=folder,
                        resource_type=resource_type,
                        use_filename=True,
                        unique_filename=True,
                        overwrite=False
                    )
                return {
                    "file_name": file_name,
                    "file_url": upload_result.get("secure_url") or upload_result.get("url"),
                    "public_id": upload_result.get("public_id"),
                    "file_size_bytes": upload_result.get("bytes", file_size),
                    "mime_type": f"{resource_type}/{upload_result.get('format', 'png')}",
                    "media_type": MediaType.VIDEO if resource_type == "video" else MediaType.IMAGE,
                    "meta_info": {
                        "provider": "cloudinary",
                        "public_id": upload_result.get("public_id"),
                        "format": upload_result.get("format"),
                        "resource_type": upload_result.get("resource_type", resource_type),
                        "width": upload_result.get("width"),
                        "height": upload_result.get("height"),
                        "created_at": upload_result.get("created_at")
                    }
                }
            except Exception as e:
                logger.error(f"Cloudinary upload_local_file_path failed: {str(e)}")

        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cloudinary upload failed. Media was not stored locally because scheduled social publishing requires a public Cloudinary URL."
        )

    @classmethod
    async def upload_file(
        cls,
        file: UploadFile,
        organization_id: int,
        folder_prefix: str = "socialai"
    ) -> Dict[str, Any]:
        """
        Reads and validates an uploaded file, then uploads to Cloudinary.
        Falls back to safe local storage simulation if Cloudinary credentials are not configured.
        """
        content = await file.read()
        file_size = len(content)

        if file_size == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot upload an empty file."
            )

        original_filename = sanitize_filename(file.filename or "upload.bin")
        content_type = file.content_type or "application/octet-stream"
        media_type, ext = cls.validate_file(original_filename, content_type, file_size)

        folder = f"{folder_prefix}/org_{organization_id}"
        resource_type = "video" if media_type == MediaType.VIDEO else "image"

        # Check if Cloudinary is fully configured
        if cls.is_configured():
            try:
                cls._init_cloudinary()
                # Upload directly to Cloudinary
                upload_result = cloudinary.uploader.upload(
                    content,
                    folder=folder,
                    resource_type=resource_type,
                    use_filename=True,
                    unique_filename=True,
                    overwrite=False
                )

                return {
                    "file_name": original_filename,
                    "file_url": upload_result.get("secure_url") or upload_result.get("url"),
                    "public_id": upload_result.get("public_id"),
                    "file_size_bytes": upload_result.get("bytes", file_size),
                    "mime_type": content_type or f"{resource_type}/{upload_result.get('format', ext.strip('.'))}",
                    "media_type": media_type,
                    "meta_info": {
                        "provider": "cloudinary",
                        "public_id": upload_result.get("public_id"),
                        "format": upload_result.get("format"),
                        "resource_type": upload_result.get("resource_type", resource_type),
                        "width": upload_result.get("width"),
                        "height": upload_result.get("height"),
                        "duration": upload_result.get("duration"),
                        "created_at": upload_result.get("created_at"),
                        "etag": upload_result.get("etag")
                    }
                }
            except Exception as e:
                logger.error(f"Cloudinary upload failed: {str(e)}. Falling back to local storage.")
                if "Invalid credentials" in str(e) or "Must supply api_key" in str(e):
                    # Continue to fallback below
                    pass
                else:
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=f"Cloudinary upload error: {str(e)}"
                    )

        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cloudinary is unavailable. Upload cancelled; configure valid Cloudinary credentials before scheduling media."
        )

    @classmethod
    def delete_file(cls, public_id: str, media_type: MediaType) -> bool:
        """Deletes a file from Cloudinary given its public_id."""
        if not public_id:
            return False

        if cls.is_configured():
            try:
                cls._init_cloudinary()
                resource_type = "video" if media_type == MediaType.VIDEO else "image"
                res = cloudinary.uploader.destroy(public_id, resource_type=resource_type)
                logger.info(f"Cloudinary deletion result for {public_id}: {res}")
                return res.get("result") in ["ok", "not found"]
            except Exception as e:
                logger.error(f"Failed to delete {public_id} from Cloudinary: {str(e)}")
                return False

        return True

    @classmethod
    def generate_upload_signature(cls, organization_id: int, folder_prefix: str = "socialai") -> Dict[str, Any]:
        """
        Generates a secure timestamped upload signature for direct browser uploads.
        Never returns the API Secret.
        """
        timestamp = int(time.time())
        folder = f"{folder_prefix}/org_{organization_id}"
        params_to_sign = {
            "folder": folder,
            "timestamp": timestamp
        }

        if cls.is_configured():
            signature = cloudinary.utils.api_sign_request(
                params_to_sign,
                settings.CLOUDINARY_API_SECRET
            )
            api_key = settings.CLOUDINARY_API_KEY
            cloud_name = settings.CLOUDINARY_CLOUD_NAME
        else:
            # Simulated signature for development mode
            import hashlib
            signature = hashlib.sha1(f"folder={folder}&timestamp={timestamp}".encode("utf-8")).hexdigest()
            api_key = "demo_key"
            cloud_name = settings.CLOUDINARY_CLOUD_NAME or "demo-socialai"

        return {
            "timestamp": timestamp,
            "signature": signature,
            "api_key": api_key,
            "cloud_name": cloud_name,
            "folder": folder
        }
