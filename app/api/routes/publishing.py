from datetime import datetime
import asyncio
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db.dependencies import get_db
from app.db.session import SessionLocal
from app.models.user import User
from app.models.content import Content
from app.models.social_account import SocialAccount
from app.models.scheduled_post import ScheduledPost
from app.models.publish_attempt import PublishAttempt
from app.integrations.social.instagram import InstagramClient
from app.integrations.social.facebook import FacebookClient
from app.integrations.social.youtube import YouTubeClient
from app.integrations.social.twitter import TwitterClient
from app.integrations.social.snapchat import SnapchatClient
from app.integrations.social.tiktok import TikTokClient
from app.integrations.social.linkedin import LinkedInClient
from app.integrations.social.pinterest import PinterestClient
from app.core.security import decrypt_token, encrypt_token
from app.core.permissions import require_creator
from app.utils.enums import ContentStatus, PublishAttemptStatus, PlatformType

router = APIRouter(prefix="/publishing", tags=["Instant Publishing"])


class PublishRequestPayload(BaseModel):
    social_account_ids: Optional[List[int]] = None


def get_client_class(platform_name: Any):
    try:
        p_enum = PlatformType(platform_name)
    except Exception:
        p_enum = PlatformType.INSTAGRAM

    mapping = {
        PlatformType.INSTAGRAM: InstagramClient,
        PlatformType.FACEBOOK: FacebookClient,
        PlatformType.YOUTUBE: YouTubeClient,
        PlatformType.TWITTER: TwitterClient,
        PlatformType.SNAPCHAT: SnapchatClient,
        PlatformType.TIKTOK: TikTokClient,
        PlatformType.LINKEDIN: LinkedInClient,
        PlatformType.PINTEREST: PinterestClient
    }
    return mapping.get(p_enum, InstagramClient)


def _trigger_delayed_analytics_sync(organization_id: int, brand_id: Optional[int] = None):
    """Fires a delayed background task to sync fresh analytics from platform APIs after publishing."""
    async def _runner():
        await asyncio.sleep(5)
        from app.db.session import SessionLocal
        from app.services.analytics_service import AnalyticsService
        db = SessionLocal()
        try:
            await AnalyticsService.sync_analytics(db=db, organization_id=organization_id, brand_id=brand_id)
        except Exception:
            pass
        finally:
            db.close()
    try:
        asyncio.create_task(_runner())
    except Exception:
        pass


async def _dispatch_to_account_with_session(content_id: int, account_id: int) -> Dict[str, Any]:
    """Isolated session dispatcher for safe concurrent execution."""
    db = SessionLocal()
    try:
        content = db.query(Content).filter(Content.id == content_id).first()
        account = db.query(SocialAccount).filter(SocialAccount.id == account_id).first()
        if not content or not account:
            return {"success": False, "error": "Content or Social Account not found"}
        return await _dispatch_to_account(content, account, db)
    finally:
        db.close()


async def _dispatch_to_account(content: Content, account: SocialAccount, db: Session) -> Dict[str, Any]:
    """Execute live post to a single social channel."""
    meta = content.meta_info or {}
    meta_fmt = str(meta.get("post_format") or meta.get("format") or "post").lower()
    if "reel" in meta_fmt or "short" in meta_fmt or "video" in meta_fmt:
        resolved_format = "reel"
    elif "story" in meta_fmt:
        resolved_format = "story"
    else:
        resolved_format = "post"
    # Mandatory Quality Check & Duplicate Gate
    if getattr(content, "validation_status", None) == "BLOCK" or getattr(content, "is_duplicate", False):
        return {
            "success": False,
            "platform": account.platform,
            "account_name": account.account_name,
            "error": "Publication blocked: Content failed automated compliance check or was flagged as a duplicate."
        }
    if content.media_asset and (getattr(content.media_asset, "validation_status", None) == "BLOCK" or getattr(content.media_asset, "is_duplicate", False)):
        return {
            "success": False,
            "platform": account.platform,
            "account_name": account.account_name,
            "error": "Publication blocked: Attached media asset failed compliance check or was flagged as a duplicate."
        }

    post = ScheduledPost(
        content_id=content.id,
        social_account_id=account.id,
        title=content.title,
        caption=content.caption or content.title,
        platform=account.platform,
        post_format=resolved_format,
        media_asset_id=content.media_asset_id,
        scheduled_time=datetime.utcnow(),
        status=ContentStatus.PUBLISHING
    )
    db.add(post)
    db.commit()
    db.refresh(post)

    p_str = (
        account.platform.value if hasattr(account.platform, "value") else str(account.platform)
    ).lower()

    from app.services.social_token_manager import SocialTokenManager, SocialAuthError
    try:
        raw_token = await SocialTokenManager.ensure_valid_token(db, account)
    except SocialAuthError as auth_err:
        error_msg = f"{p_str.capitalize()} authentication failed: {auth_err}"
        post.status = ContentStatus.FAILED
        post.publish_metadata = {"error": str(auth_err), "auth_required": True}
        attempt = PublishAttempt(
            scheduled_post_id=post.id,
            status=PublishAttemptStatus.FAILED,
            error_message=error_msg
        )
        db.add(attempt)
        db.commit()
        return {
            "success": False,
            "platform": p_str,
            "account_name": account.account_name,
            "error": error_msg
        }
    except Exception as auth_err:
        fallback_tok = decrypt_token(account.access_token) if account.access_token else None
        if fallback_tok:
            raw_token = fallback_tok
        else:
            error_msg = f"{p_str.capitalize()} token validation error: {auth_err}"
            post.status = ContentStatus.FAILED
            post.publish_metadata = {"error": str(auth_err), "auth_required": True}
            attempt = PublishAttempt(
                scheduled_post_id=post.id,
                status=PublishAttemptStatus.FAILED,
                error_message=error_msg
            )
            db.add(attempt)
            db.commit()
            return {
                "success": False,
                "platform": p_str,
                "account_name": account.account_name,
                "error": error_msg
            }

    raw_refresh = (decrypt_token(account.refresh_token) or account.refresh_token) if account.refresh_token else None
    if not raw_token:
        error_msg = f"Account '{account.account_name}' ({account.platform}) has no active access credentials configured."
        post.status = ContentStatus.FAILED
        post.publish_metadata = {"error": error_msg}
        attempt = PublishAttempt(
            scheduled_post_id=post.id,
            status=PublishAttemptStatus.FAILED,
            error_message=error_msg
        )
        db.add(attempt)
        db.commit()
        return {
            "success": False,
            "platform": account.platform,
            "account_name": account.account_name,
            "error": error_msg
        }

    client_cls = get_client_class(account.platform)
    try:
        client = client_cls(access_token=raw_token, refresh_token=raw_refresh)
    except TypeError:
        client = client_cls(access_token=raw_token)


    try:
        try:
            p_enum = PlatformType(account.platform)
        except Exception:
            p_enum = PlatformType.INSTAGRAM

        kwargs = {}
        if p_enum == PlatformType.FACEBOOK:
            kwargs["page_id"] = account.account_id or "me"
        elif p_enum == PlatformType.INSTAGRAM:
            kwargs["account_id"] = account.account_id or "me"
        elif p_enum == PlatformType.SNAPCHAT:
            kwargs["ad_account_id"] = account.account_id or (account.meta_info or {}).get("ad_account_id")

        kwargs["post_format"] = (content.meta_info or {}).get("post_format", "post")
        kwargs["media_type"] = getattr(content.media_asset, "media_type", None)
        kwargs["title"] = content.title

        # Collect media assets across media_items, media_asset, and meta_info
        media_urls = [m.file_url for m in content.media_items] if hasattr(content, "media_items") and content.media_items else []
        if content.media_asset and content.media_asset.file_url and content.media_asset.file_url not in media_urls:
            media_urls.append(content.media_asset.file_url)

        meta = content.meta_info or {}
        candidate = meta.get("mediaUrl") or meta.get("file_url") or meta.get("thumbnailUrl") or meta.get("media_url")
        if candidate and isinstance(candidate, str) and candidate not in media_urls:
            media_urls.append(candidate)
        elif isinstance(meta.get("mediaArray"), list):
            for item in meta["mediaArray"]:
                if isinstance(item, str) and item not in media_urls:
                    media_urls.append(item)

        is_video_content = any(
            str(u).lower().split("?")[0].endswith((".mp4", ".mov", ".webm")) or "/video/" in str(u).lower()
            for u in media_urls
        )
        if is_video_content and resolved_format != "story":
            kwargs["post_format"] = "reel"
            kwargs["media_type"] = "video"

        media_type = None
        if content.media_asset:
            media_type = content.media_asset.media_type.value if hasattr(content.media_asset.media_type, "value") else str(content.media_asset.media_type)
        if media_type:
            kwargs["media_type"] = media_type

        result = await client.publish_post(
            content_text=content.caption or content.title or "",
            media_urls=media_urls,
            **kwargs
        )

        refreshed_tok = result.get("access_token") or getattr(client, "access_token", None)
        if refreshed_tok and refreshed_tok != raw_token:
            account.access_token = encrypt_token(refreshed_tok)
        refreshed_refresh = result.get("refresh_token") or getattr(client, "refresh_token", None)
        if refreshed_refresh and refreshed_refresh != raw_refresh:
            account.refresh_token = encrypt_token(refreshed_refresh)
        db.commit()

        post.status = ContentStatus.PUBLISHED
        post.published_time = datetime.utcnow()
        post.platform_post_id = str(result.get("platform_post_id", ""))
        post.publish_metadata = result

        content.status = ContentStatus.PUBLISHED

        attempt = PublishAttempt(
            scheduled_post_id=post.id,
            status=PublishAttemptStatus.SUCCESS,
            response_payload=result
        )
        db.add(attempt)
        db.commit()

        try:
            from app.services.notification_service import NotificationService
            plat_name = str(account.platform.value if hasattr(account.platform, 'value') else account.platform).title()
            NotificationService.create_notification(
                db=db,
                user_id=content.creator_id,
                organization_id=content.organization_id,
                title=f"Content Published to {plat_name}",
                message=f"'{content.title or 'Creative post'}' published successfully to @{account.account_name}.",
                event_type="publish_success",
                severity="success",
                link="/dashboard/schedule",
                metadata={"content_id": content.id, "platform": str(account.platform), "account_name": account.account_name},
                dedup_key=f"instant_pub_success_{content.id}_{account.id}_{post.id}"
            )
        except Exception as notif_err:
            pass

        return {
            "success": True,
            "message": f"Content published successfully to {account.platform}!",
            "status": "published",
            "platform": account.platform,
            "account_name": account.account_name,
            "result": result
        }
    except Exception as e:
        error_msg = str(e)
        post.status = ContentStatus.FAILED
        post.publish_metadata = {"error": error_msg}
        attempt = PublishAttempt(
            scheduled_post_id=post.id,
            status=PublishAttemptStatus.FAILED,
            error_message=error_msg
        )
        db.add(attempt)
        db.commit()

        try:
            from app.services.notification_service import NotificationService
            plat_name = str(account.platform.value if hasattr(account.platform, 'value') else account.platform).title()
            NotificationService.create_notification(
                db=db,
                user_id=content.creator_id,
                organization_id=content.organization_id,
                title=f"Publish Failed on {plat_name}",
                message=f"Failed to publish '{content.title or 'Post'}': {error_msg[:120]}",
                event_type="publish_failed",
                severity="error",
                link="/dashboard/schedule",
                metadata={"content_id": content.id, "platform": str(account.platform), "error": error_msg},
                dedup_key=f"instant_pub_fail_{content.id}_{account.id}_{post.id}"
            )
        except Exception as notif_err:
            pass

        return {
            "success": False,
            "platform": account.platform,
            "account_name": account.account_name,
            "error": error_msg
        }


@router.post("/instant/{content_id}/{social_account_id}")
async def publish_now(
    content_id: int,
    social_account_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Trigger live publishing of content to the selected social account."""
    from app.models.brand_profile import BrandProfile
    content = db.query(Content).join(BrandProfile, Content.brand_profile_id == BrandProfile.id).filter(
        Content.id == content_id,
        BrandProfile.organization_id == current_user.organization_id
    ).first()
    if not content:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found or unauthorized.")

    primary_account = db.query(SocialAccount).join(BrandProfile, SocialAccount.brand_profile_id == BrandProfile.id).filter(
        SocialAccount.id == social_account_id,
        BrandProfile.organization_id == current_user.organization_id
    ).first()
    if not primary_account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Social account not found or unauthorized.")

    # This legacy single-account endpoint must respect the selected account.
    # Multi-account publishing is handled by /publish-now with explicit IDs.
    target_accounts = [primary_account]

    # Concurrently dispatch all accounts
    tasks = [_dispatch_to_account_with_session(content.id, acc.id) for acc in target_accounts]
    dispatch_results = await asyncio.gather(*tasks, return_exceptions=True)

    results = []
    errors = []
    for acc, res in zip(target_accounts, dispatch_results):
        if isinstance(res, dict) and res.get("success"):
            results.append(res)
        else:
            err = res.get("error") if isinstance(res, dict) else str(res)
            errors.append(f"{acc.platform}: {err}")

    # Mark content published if at least one succeeded
    if results:
        content.status = ContentStatus.PUBLISHED
        _trigger_delayed_analytics_sync(current_user.organization_id, content.brand_profile_id)
    else:
        content.status = ContentStatus.FAILED
    db.commit()

    return {
        "message": f"Simultaneously dispatched across {len(target_accounts)} channels ({len(results)} succeeded, {len(errors)} failed).",
        "content_id": content.id,
        "status": "published" if results else "failed",
        "published_accounts": [acc.account_name for acc in target_accounts if any(r.get("account_name") == acc.account_name and r.get("success") for r in results)],
        "results": results,
        "errors": errors
    }


@router.post("/publish-now/{content_id}")
async def publish_content_now(
    content_id: int,
    payload: Optional[PublishRequestPayload] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Publish content live across selected or ALL connected social accounts concurrently."""
    from app.models.brand_profile import BrandProfile
    content = db.query(Content).join(BrandProfile, Content.brand_profile_id == BrandProfile.id).filter(
        Content.id == content_id,
        BrandProfile.organization_id == current_user.organization_id
    ).first()
    if not content:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found or unauthorized.")

    # Find connected accounts for this brand or organization
    query = db.query(SocialAccount).join(BrandProfile, SocialAccount.brand_profile_id == BrandProfile.id).filter(
        BrandProfile.organization_id == current_user.organization_id,
        SocialAccount.is_connected == True
    )

    if payload and payload.social_account_ids:
        query = query.filter(SocialAccount.id.in_(payload.social_account_ids))

    accounts = query.all()
    if not accounts and not (payload and payload.social_account_ids):
        accounts = db.query(SocialAccount).filter(
            SocialAccount.brand_profile_id == content.brand_profile_id,
            SocialAccount.is_connected == True
        ).all()

    if accounts and len(accounts) > 0:
        # Deduplicate target accounts by platform
        seen_platforms = set()
        target_accounts = []
        for acc in accounts:
            plat_key = str(acc.platform).lower()
            if plat_key not in seen_platforms:
                seen_platforms.add(plat_key)
                target_accounts.append(acc)

        # Concurrently dispatch all accounts
        tasks = [_dispatch_to_account_with_session(content.id, acc.id) for acc in target_accounts]
        dispatch_results = await asyncio.gather(*tasks, return_exceptions=True)

        results = []
        errors = []
        for acc, res in zip(target_accounts, dispatch_results):
            if isinstance(res, dict) and res.get("success"):
                results.append(res)
            else:
                err = res.get("error") if isinstance(res, dict) else str(res)
                errors.append(f"{acc.platform}: {err}")

        if results:
            content.status = ContentStatus.PUBLISHED
            _trigger_delayed_analytics_sync(current_user.organization_id, content.brand_profile_id)
        else:
            content.status = ContentStatus.FAILED
        db.commit()

        plat_names = ", ".join(seen_platforms)
        return {
            "message": f"Published across {len(results)} social channels ({plat_names})!",
            "content_id": content.id,
            "status": "published" if results else "failed",
            "published_accounts": [r.get("account_name") for r in results if r.get("success")],
            "results": results,
            "errors": errors
        }

    # If specific accounts were requested but none connected
    if payload and payload.social_account_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="None of the selected accounts are connected. Please connect accounts in Social Accounts first."
        )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="No connected social accounts are available. Connect and verify an account before publishing."
    )

