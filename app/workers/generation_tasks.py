import asyncio
from app.workers.celery_app import celery_app
from app.db.session import SessionLocal
from app.models.generation_job import GenerationJob
from app.integrations.ai.text_provider import AITextProvider
from app.integrations.ai.image_provider import AIImageProvider
from app.utils.enums import JobStatus, GenerationType
from app.core.logging import logger


@celery_app.task(name="app.workers.generation_tasks.execute_generation_job")
def execute_generation_job(job_id: int):
    """Celery task to run long-running AI generations."""
    db = SessionLocal()
    try:
        job = db.query(GenerationJob).filter(GenerationJob.id == job_id).first()
        if not job:
            return

        job.status = JobStatus.PROCESSING
        db.commit()

        loop = asyncio.get_event_loop()
        if job.job_type in [GenerationType.TEXT_CAPTION, GenerationType.TEXT_HASHTAGS, GenerationType.TEXT_ARTICLE]:
            result = loop.run_until_complete(
                AITextProvider.generate_caption(
                    prompt=job.prompt,
                    tone=job.parameters.get("tone", "engaging"),
                    target_platforms=job.parameters.get("platforms", []),
                    provider=job.parameters.get("provider", "openai")
                )
            )
            job.result_data = result
            job.tokens_used = result.get("tokens_used", 100)
            job.status = JobStatus.COMPLETED

        elif job.job_type == GenerationType.IMAGE_GENERATION:
            result = loop.run_until_complete(
                AIImageProvider.generate_image(
                    prompt=job.prompt,
                    aspect_ratio=job.parameters.get("aspect_ratio", "1:1"),
                    style=job.parameters.get("style", "modern")
                )
            )
            job.result_data = result
            job.status = JobStatus.COMPLETED

        db.commit()
    except Exception as e:
        logger.error(f"Generation task failed: {str(e)}")
        if job:
            job.status = JobStatus.FAILED
            job.error_message = str(e)
            db.commit()
    finally:
        db.close()
