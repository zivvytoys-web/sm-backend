import os
from fastapi import UploadFile, HTTPException, status
from app.integrations.storage.cloudinary_storage import CloudinaryStorageProvider
from app.utils.enums import MediaType


class UploadMiddleware:
    """Middleware validator for uploaded files before reaching controllers/storage."""

    @staticmethod
    def validate_upload(file: UploadFile, max_image_mb: int = 10, max_video_mb: int = 100):
        if not file or not file.filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No file was uploaded or file name is missing."
            )

        filename = file.filename
        content_type = file.content_type or ""
        file_size = getattr(file, "size", 0)

        # Utilize CloudinaryStorageProvider format and size check
        return CloudinaryStorageProvider.validate_file(filename, content_type, file_size)


__all__ = ["UploadMiddleware"]
