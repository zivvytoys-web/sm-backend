import os
import uuid
import aiofiles
from fastapi import UploadFile
from app.core.config import settings
from app.core.logging import logger
from app.utils.validators import sanitize_filename


class ObjectStorageProvider:
    """Handles file asset uploads either to Local Disk or Amazon S3."""

    @classmethod
    async def upload_file(cls, file: UploadFile, folder: str = "media") -> str:
        safe_name = sanitize_filename(file.filename or "upload.jpg")
        unique_name = f"{uuid.uuid4().hex}_{safe_name}"

        if settings.STORAGE_TYPE == "s3" and settings.AWS_ACCESS_KEY_ID:
            # S3 upload flow using boto3
            try:
                import boto3
                s3_client = boto3.client(
                    "s3",
                    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                    region_name=settings.AWS_REGION
                )
                key = f"{folder}/{unique_name}"
                s3_client.upload_fileobj(file.file, settings.AWS_S3_BUCKET, key)
                return f"https://{settings.AWS_S3_BUCKET}.s3.{settings.AWS_REGION}.amazonaws.com/{key}"
            except Exception as e:
                logger.error(f"S3 upload failed, falling back to local: {str(e)}")

        # Local storage fallback
        base_dir = os.path.join(settings.LOCAL_STORAGE_DIR, folder)
        os.makedirs(base_dir, exist_ok=True)
        file_path = os.path.join(base_dir, unique_name)

        async with aiofiles.open(file_path, "wb") as out_file:
            content = await file.read()
            await out_file.write(content)

        return f"/uploads/{folder}/{unique_name}"
