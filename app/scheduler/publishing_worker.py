import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.scheduled_post import ScheduledPost
from app.models.content import Content
from app.models.social_account import SocialAccount
from app.models.publish_attempt import PublishAttempt
from app.integrations.social import (
    InstagramClient,
    FacebookClient,
    LinkedInClient,
    YouTubeClient,
    TwitterClient,
    SnapchatClient,
    TikTokClient,
    PinterestClient
)
from app.utils.enums import ContentStatus, PublishAttemptStatus, PlatformType
from app.core.logging import logger
from app.core.security import decrypt_token, encrypt_token

CLIENT_MAP = {
    "instagram": InstagramClient,
    "facebook": FacebookClient,
    "linkedin": LinkedInClient,
    "youtube": YouTubeClient,
    "twitter": TwitterClient,
    "tiktok": TikTokClient,
    "pinterest": PinterestClient,
    "snapchat": SnapchatClient,
    PlatformType.INSTAGRAM: InstagramClient,
    PlatformType.FACEBOOK: FacebookClient,
    PlatformType.LINKEDIN: LinkedInClient,
    PlatformType.YOUTUBE: YouTubeClient,
    PlatformType.TWITTER: TwitterClient,
    PlatformType.SNAPCHAT: SnapchatClient,
    PlatformType.TIKTOK: TikTokClient,
    PlatformType.PINTEREST: PinterestClient,
}


class PublishingWorker:
    """Background publishing worker that executes due scheduled posts concurrently from MySQL."""

    _is_running = False

    @classmethod
    async def publish_single_post(cls, db: Session, post_id: int, is_manual: bool = False) -> Dict[str, Any]:
        post = db.query(ScheduledPost).filter(ScheduledPost.id == post_id).first()
        if not post:
            return {"success": False, "error": "Scheduled post not found"}

        status_str = str(post.status.value if hasattr(post.status, "value") else (post.status or "")).lower()
        if status_str in ["published"]:
            return {"success": True, "already_published": True, "platform_post_id": post.platform_post_id}

        if status_str in ["cancelled", "canceled", "archived"]:
            return {"success": False, "error": f"Post status is '{post.status}' and cannot be published."}

        if not is_manual and post.scheduled_time:
            now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
            sched_time = post.scheduled_time
            if sched_time.tzinfo is not None:
                sched_time = sched_time.astimezone(timezone.utc).replace(tzinfo=None)
            if sched_time > now_utc:
                return {"success": False, "error": f"Post {post.id} is not due yet (scheduled for {sched_time})."}

        content = db.query(Content).filter(Content.id == post.content_id).first() if post.content_id else None
        has_post_media = bool(post.media_asset and getattr(post.media_asset, "file_url", None))
        has_post_text = bool((post.caption and post.caption.strip()) or (post.title and post.title.strip()))
        
        if not content and not has_post_media and not has_post_text:
            post.status = ContentStatus.FAILED
            post.error_message = "Content record or post media/caption not found."
            db.commit()
            return {"success": False, "error": post.error_message}

        # Mandatory Quality & Duplicate Gate
        # Prevent any content flagged as BLOCK or CONFIRMED_DUPLICATE from being published
        if content:
            if getattr(content, "validation_status", None) == "BLOCK" or getattr(content, "is_duplicate", False):
                err = "Publication blocked: Content failed automated compliance check or was flagged as a duplicate."
                post.status = "failed"
                post.error_message = err
                post.publish_metadata = {"blocked_by_quality_gate": True, "error": err}
                db.commit()
                return {"success": False, "error": err, "blocked_by_quality_gate": True}

            if content.media_asset:
                if getattr(content.media_asset, "validation_status", None) == "BLOCK" or getattr(content.media_asset, "is_duplicate", False):
                    err = "Publication blocked: Attached media asset failed compliance check or was flagged as a duplicate."
                    post.status = "failed"
                    post.error_message = err
                    post.publish_metadata = {"blocked_by_quality_gate": True, "error": err}
                    db.commit()
                    return {"success": False, "error": err, "blocked_by_quality_gate": True}

        account = post.social_account
        platform_key = (
            (account.platform.value if hasattr(account.platform, "value") else str(account.platform))
            if account
            else (post.platform.value if hasattr(post.platform, "value") else str(post.platform or "instagram"))
        ).lower()

        caption_text = (content.caption if content and content.caption else "") or post.caption or post.title or ""
        tags = (content.hashtags if content and content.hashtags else []) or (post.hashtags or [])
        if tags:
            hashtag_str = ' '.join(f'#{tag.lstrip("#")}' for tag in tags)
            caption_text = f"{caption_text}\n\n{hashtag_str}".strip()

        if not account:
            # Fallback to connected account for this platform
            account = db.query(SocialAccount).filter(
                SocialAccount.platform == platform_key,
                SocialAccount.is_connected == True
            ).first()

        if not account:
            err = f"No connected account found for platform '{platform_key}'. Please connect your account."
            post.status = "failed"
            post.error_message = err
            db.commit()
            return {"success": False, "error": err}

        # Atomically claim this scheduled row before calling a provider. This
        # prevents concurrent worker ticks/retries from publishing it twice.
        claimed = db.query(ScheduledPost).filter(
            ScheduledPost.id == post.id,
            ScheduledPost.status.in_(["scheduled", ContentStatus.SCHEDULED])
        ).update({ScheduledPost.status: "publishing"}, synchronize_session=False)
        if claimed != 1:
            db.rollback()
            return {"success": False, "error": "Post is already being processed or is no longer scheduled."}
        db.commit()
        db.refresh(post)

        # Ensure valid access token for social provider with automatic backend token renewal (Google/YouTube & Meta/Instagram/Facebook)
        from app.services.social_token_manager import SocialTokenManager, SocialAuthError
        try:
            raw_token = await SocialTokenManager.ensure_valid_token(db, account)
        except SocialAuthError as auth_err:
            post.status = "failed"
            post.error_message = f"{platform_key.capitalize()} authentication failed: {auth_err}"
            post.publish_metadata = {"error": str(auth_err), "auth_required": True}
            db.commit()
            return {"success": False, "error": str(auth_err), "platform": platform_key}
        except Exception as auth_err:
            fallback_tok = decrypt_token(account.access_token) if account.access_token else None
            if fallback_tok:
                raw_token = fallback_tok
            else:
                post.status = "failed"
                post.error_message = f"{platform_key.capitalize()} token validation error: {auth_err}"
                post.publish_metadata = {"error": str(auth_err), "auth_required": True}
                db.commit()
                return {"success": False, "error": str(auth_err), "platform": platform_key}

        raw_refresh = (decrypt_token(account.refresh_token) or account.refresh_token) if account.refresh_token else None
        # Collect media URLs from all possible sources (content.media_asset, post.media_asset, meta_info, media_items)
        media_urls = []
        if content and content.media_asset and content.media_asset.file_url:
            media_urls.append(content.media_asset.file_url)
        elif post.media_asset and getattr(post.media_asset, "file_url", None):
            media_urls.append(post.media_asset.file_url)
        elif content and hasattr(content, "media_items") and content.media_items:
            for m in content.media_items:
                if getattr(m, "file_url", None):
                    media_urls.append(m.file_url)

        meta = (content.meta_info if content else None) or post.publish_metadata or {}
        media_type = None
        if content and content.media_asset:
            media_type = content.media_asset.media_type.value if hasattr(content.media_asset.media_type, "value") else str(content.media_asset.media_type)
        elif post.media_asset:
            media_type = post.media_asset.media_type.value if hasattr(post.media_asset.media_type, "value") else str(post.media_asset.media_type)

        if not media_urls:
            candidate = meta.get("mediaUrl") or meta.get("file_url") or meta.get("thumbnailUrl") or meta.get("media_url")
            if candidate and isinstance(candidate, str) and (candidate.startswith("http") or candidate.startswith("/")):
                media_urls.append(candidate)
            elif isinstance(meta.get("mediaArray"), list) and len(meta["mediaArray"]) > 0:
                for item in meta["mediaArray"]:
                    if isinstance(item, str) and (item.startswith("http") or item.startswith("/")):
                        media_urls.append(item)

        # Resolve numerical account ID
        target_account_id = account.account_id
        if (not target_account_id or target_account_id.startswith("instagram_")) and isinstance(account.meta_info, dict):
            if account.meta_info.get("account_id"):
                target_account_id = str(account.meta_info["account_id"])

        attempt_count = db.query(PublishAttempt).filter(
            PublishAttempt.scheduled_post_id == post.id
        ).count() + 1

        try:
            p_enum = PlatformType(account.platform)
        except Exception:
            p_enum = PlatformType.INSTAGRAM

        client_cls = CLIENT_MAP.get(p_enum, InstagramClient)
        try:
            client = client_cls(access_token=raw_token, refresh_token=raw_refresh)
        except TypeError:
            client = client_cls(access_token=raw_token)

        kwargs = {}
        if p_enum == PlatformType.FACEBOOK:
            kwargs = {"page_id": target_account_id or "me"}
        elif p_enum == PlatformType.INSTAGRAM:
            kwargs = {"account_id": target_account_id}
        elif p_enum == PlatformType.SNAPCHAT:
            kwargs = {"ad_account_id": target_account_id}
        if media_type:
            kwargs["media_type"] = media_type
        elif getattr(post, "media_asset", None):
            kwargs["media_type"] = getattr(post.media_asset, "media_type", None)
        if hasattr(post, "post_format"):
            kwargs["post_format"] = post.post_format
        if getattr(post, "title", None) or getattr(content, "title", None):
            kwargs["title"] = getattr(post, "title", None) or getattr(content, "title", None)

        try:
            logger.info(f"PublishingWorker: executing post #{post.id} to {account.platform} (account_id={target_account_id})...")
            result = await client.publish_post(
                content_text=caption_text,
            media_urls=media_urls,
                **kwargs
            )

            # Success
            post.status = "published"
            post.published_time = datetime.utcnow()
            post.platform_post_id = str(result.get("platform_post_id", ""))
            post.publish_metadata = result
            post.error_message = None

            # Persist updated access token if auto-refreshed
            refreshed_tok = result.get("access_token") or getattr(client, "access_token", None)
            if refreshed_tok and refreshed_tok != raw_token:
                account.access_token = encrypt_token(refreshed_tok)
                db.commit()

            # Update content status if associated
            if content:
                content.status = ContentStatus.PUBLISHED

            attempt = PublishAttempt(
                scheduled_post_id=post.id,
                attempt_number=attempt_count,
                status=PublishAttemptStatus.SUCCESS,
                response_payload=result
            )
            db.add(attempt)
            db.commit()
            logger.info(f"Successfully auto-published post #{post.id} to {account.platform}! (Live ID: {post.platform_post_id})")

            try:
                from app.services.notification_service import NotificationService
                plat_label = str(account.platform.value if hasattr(account.platform, 'value') else account.platform).title()
                post_summary = post.title or (post.caption[:60] + '...' if post.caption else 'Scheduled Post')
                NotificationService.create_notification(
                    db=db,
                    user_id=post.user_id,
                    organization_id=post.organization_id,
                    title=f"Post Published to {plat_label}",
                    message=f"'{post_summary}' was successfully published.",
                    event_type="publish_success",
                    severity="success",
                    link="/dashboard/schedule",
                    metadata={
                        "post_id": post.id,
                        "platform": str(account.platform),
                        "platform_post_id": post.platform_post_id
                    },
                    dedup_key=f"worker_publish_success_{post.id}_{post.platform_post_id or 'pub'}"
                )
            except Exception as notif_err:
                logger.warning(f"Could not record publish success notification: {notif_err}")

            return {"success": True, "result": result, "platform": account.platform}

        except Exception as e:
            error_msg = str(e)
            from app.services.youtube_token_manager import YouTubeAuthError, YouTubeQuotaError

            # Categorize error: is it fatal (auth/permissions/account missing/quota) or transient (network/timeout)
            is_auth_error = isinstance(e, YouTubeAuthError)
            is_quota_error = isinstance(e, YouTubeQuotaError)
            is_fatal = is_auth_error or is_quota_error or any(keyword in error_msg.lower() for keyword in [
                "no connected account", "invalid token", "token expired",
                "not authorized", "permission denied", "oauth", "cancelled",
                "missing credentials", "reconnect your youtube", "reauthorization"
            ])

            max_retries = 3
            if not is_fatal and attempt_count < max_retries and not is_manual:
                # Retry with backoff (5m, 10m)
                backoff_mins = 5 * attempt_count
                post.status = "scheduled"
                post.scheduled_time = datetime.utcnow() + timedelta(minutes=backoff_mins)
                post.error_message = f"Attempt {attempt_count}/{max_retries} failed: {error_msg}. Retrying in {backoff_mins}m."
            else:
                post.status = "failed"
                post.error_message = error_msg
                post.publish_metadata = {"error": error_msg, "total_attempts": attempt_count}

                if post.content_id:
                    content = db.query(Content).filter(Content.id == post.content_id).first()
                    if content:
                        content.status = ContentStatus.FAILED

            # If other posts for this content succeeded, don't overwrite content.status to FAILED
            if content:
                sibling_posts = db.query(ScheduledPost).filter(
                    ScheduledPost.content_id == content.id,
                    ScheduledPost.id != post.id
                ).all()
                any_sibling_published = any(s.status == ContentStatus.PUBLISHED for s in sibling_posts)
                if not any_sibling_published:
                    content.status = ContentStatus.FAILED

            attempt = PublishAttempt(
                scheduled_post_id=post.id,
                attempt_number=attempt_count,
                status=PublishAttemptStatus.FAILED,
                error_message=error_msg
            )
            db.add(attempt)
            db.commit()
            logger.warning(f"Auto-publishing post #{post.id} to {account.platform} failed: {error_msg}")

            if post.status == "failed":
                try:
                    from app.services.notification_service import NotificationService
                    plat_label = str(account.platform.value if hasattr(account.platform, 'value') else account.platform).title()
                    NotificationService.create_notification(
                        db=db,
                        user_id=post.user_id,
                        organization_id=post.organization_id,
                        title=f"Publish Failed on {plat_label}",
                        message=f"Failed to publish post: {error_msg[:140]}",
                        event_type="publish_failed",
                        severity="error",
                        link="/dashboard/schedule",
                        metadata={"post_id": post.id, "error": error_msg, "platform": str(account.platform)},
                        dedup_key=f"worker_publish_failed_{post.id}_{attempt_count}"
                    )
                except Exception as notif_err:
                    logger.warning(f"Could not record publish failure notification: {notif_err}")

            return {"success": False, "error": error_msg, "platform": account.platform}

    @classmethod
    async def _safe_publish_post(cls, post_id: int) -> Dict[str, Any]:
        """Worker task with dedicated database session for safe concurrent publishing."""
        db = SessionLocal()
        try:
            return await cls.publish_single_post(db, post_id)
        except Exception as e:
            logger.error(f"Concurrent publishing task error for post #{post_id}: {e}")
            return {"success": False, "error": str(e)}
        finally:
            db.close()

    @classmethod
    async def process_due_posts(cls) -> int:
        """
        Finds all due scheduled posts and dispatches publishing jobs
        concurrently across all target accounts rather than sequentially.
        """
        db = SessionLocal()
        published_count = 0
        try:
            now = datetime.utcnow()

            # Recovery: check for any posts stuck in 'publishing' state for > 15 minutes (e.g. server crash during publish)
            stuck_cutoff = now - timedelta(minutes=15)
            stuck_posts = db.query(ScheduledPost).filter(
                ScheduledPost.status == "publishing",
                ScheduledPost.updated_at <= stuck_cutoff
            ).all()
            for stuck in stuck_posts:
                logger.warning(f"[PublishingWorker] Recovering stuck post #{stuck.id} left in 'publishing' state.")
                stuck.status = "failed"
                stuck.error_message = "Publishing interrupted due to server restart or timeout. Please retry."
                db.commit()

            due_posts = db.query(ScheduledPost).filter(
                ScheduledPost.status.in_(["scheduled", ContentStatus.SCHEDULED]),
                ScheduledPost.scheduled_time <= now
            ).all()

            if due_posts:
                logger.info(f"[PublishingWorker] Found {len(due_posts)} scheduled posts due. Dispatching concurrently...")
                post_ids = [p.id for p in due_posts]
                # Close read session before concurrent writes
                db.close()

                # Dispatch all due posts simultaneously
                tasks = [cls._safe_publish_post(pid) for pid in post_ids]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for r in results:
                    if isinstance(r, dict) and r.get("success"):
                        published_count += 1
                return published_count
        except Exception as e:
            logger.error(f"Error checking due scheduled posts: {str(e)}")
        finally:
            try:
                db.close()
            except Exception:
                pass
        return published_count

    @classmethod
    async def start_background_loop(cls, poll_interval_seconds: int = 15):
        """Continuous background loop running during FastAPI server lifespan."""
        cls._is_running = True
        logger.info("Starting Social AI Platform background scheduler worker loop...")
        while cls._is_running:
            try:
                await cls.process_due_posts()
            except Exception as e:
                logger.error(f"Scheduler worker tick error: {str(e)}")
            try:
                from app.services.autonomous_publisher import publish_due_posts
                await publish_due_posts()
            except Exception as e:
                logger.error(f"Autonomous publisher tick error: {str(e)}")
            await asyncio.sleep(poll_interval_seconds)

    @classmethod
    def stop_background_loop(cls):
        cls._is_running = False
        logger.info("Stopped Social AI Platform background scheduler worker.")
