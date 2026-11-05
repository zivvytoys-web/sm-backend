from datetime import date
import asyncio
from app.workers.celery_app import celery_app
from app.db.session import SessionLocal
from app.models.social_account import SocialAccount
from app.models.analytics import Analytics
from app.integrations.social.instagram import InstagramClient
from app.integrations.social.facebook import FacebookClient
from app.integrations.social.linkedin import LinkedInClient
from app.integrations.social.youtube import YouTubeClient
from app.utils.enums import PlatformType
from app.core.logging import logger


CLIENT_MAP = {
    PlatformType.INSTAGRAM: InstagramClient,
    PlatformType.FACEBOOK: FacebookClient,
    PlatformType.LINKEDIN: LinkedInClient,
    PlatformType.YOUTUBE: YouTubeClient,
}


@celery_app.task(name="app.workers.analytics_tasks.sync_all_social_analytics")
def sync_all_social_analytics():
    """Daily sync of social platform metrics into database."""
    db = SessionLocal()
    try:
        accounts = db.query(SocialAccount).filter(SocialAccount.is_connected == True).all()
        loop = asyncio.get_event_loop()

        for account in accounts:
            client_cls = CLIENT_MAP.get(account.platform, InstagramClient)
            client = client_cls(access_token=account.access_token)

            try:
                metrics = loop.run_until_complete(client.fetch_analytics(account.account_id))
                today = date.today()

                record = db.query(Analytics).filter(
                    Analytics.social_account_id == account.id,
                    Analytics.date == today
                ).first()

                if not record:
                    record = Analytics(
                        social_account_id=account.id,
                        date=today,
                        impressions=metrics.get("impressions", 0),
                        reach=metrics.get("reach", 0),
                        engagements=metrics.get("engagements", 0),
                        likes=metrics.get("likes", 0),
                        comments=metrics.get("comments", 0),
                        shares=metrics.get("shares", 0),
                        clicks=metrics.get("clicks", 0),
                        engagement_rate=metrics.get("engagement_rate", 0.0),
                        raw_metrics=metrics
                    )
                    db.add(record)
                else:
                    record.impressions = metrics.get("impressions", 0)
                    record.reach = metrics.get("reach", 0)
                    record.engagements = metrics.get("engagements", 0)
                    record.likes = metrics.get("likes", 0)
                    record.comments = metrics.get("comments", 0)
                    record.shares = metrics.get("shares", 0)
                    record.clicks = metrics.get("clicks", 0)
                    record.engagement_rate = metrics.get("engagement_rate", 0.0)

                db.commit()
            except Exception as e:
                logger.error(f"Analytics sync error for account {account.id}: {str(e)}")

    finally:
        db.close()
