from datetime import datetime, timezone, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_, func

from app.utils.enums import ContentStatus

from app.db.dependencies import get_db
from app.models.user import User
from app.models.content import Content
from app.models.brand_profile import BrandProfile
from app.models.social_account import SocialAccount
from app.models.scheduled_post import ScheduledPost
from app.models.schedule_rule import ScheduleRule
from app.models.media_asset import MediaAsset
from app.models.collection import Collection
from app.schemas.scheduling import (
    ScheduledPostCreate,
    ScheduledPostUpdate,
    ScheduledPostResponse
)
from app.schemas.scheduling_rule import (
    ScheduleRuleCreate,
    ScheduleRuleUpdate,
    ScheduleRuleResponse
)
from app.services.scheduler_engine import SchedulerEngine
from app.scheduler.schedule_manager import ScheduleManager
from app.scheduler.publishing_worker import PublishingWorker
from app.core.permissions import get_current_active_user, require_creator

router = APIRouter(prefix="/scheduler", tags=["Content Scheduler"])


def to_utc_iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    if dt.tzinfo is None:
        return f"{dt.isoformat()}+00:00"
    return dt.astimezone(timezone.utc).isoformat()


def normalize_and_validate_schedule_time(dt: datetime, tz_name: str = "UTC", allow_past: bool = False) -> datetime:
    import pytz
    if not tz_name:
        tz_name = "UTC"
    try:
        tz = pytz.timezone(tz_name)
    except Exception:
        tz = pytz.UTC

    if dt.tzinfo is None:
        local_dt = tz.localize(dt)
    else:
        local_dt = dt.astimezone(tz)

    utc_dt = local_dt.astimezone(pytz.UTC)
    naive_utc = utc_dt.replace(tzinfo=None)

    if not allow_past:
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        if naive_utc < now_utc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Scheduled time cannot be in the past."
            )
    return naive_utc


def serialize_post(post: ScheduledPost) -> dict:
    return {
        "id": post.id,
        "title": post.title,
        "caption": post.caption,
        "platform": post.platform,
        "scheduled_time": to_utc_iso(post.scheduled_time),
        "timezone": post.timezone,
        "status": post.status,
    }


def _format_scheduled_response(post: ScheduledPost) -> ScheduledPostResponse:
    resp = ScheduledPostResponse.from_orm(post)
    if post.social_account:
        resp.platform = post.social_account.platform
        resp.account_name = post.social_account.account_name
    if post.content:
        resp.content_title = post.content.title or post.content.caption
    meta = post.publish_metadata or {}
    resp.error_message = meta.get("error") or meta.get("api_error")
    if not resp.error_message and post.publish_attempts:
        for a in reversed(post.publish_attempts):
            if a.error_message:
                resp.error_message = a.error_message
                break
    return resp


@router.get("/queue", response_model=List[ScheduledPostResponse])
def get_scheduled_queue(
    status: Optional[str] = None,
    status_filter: Optional[str] = None,
    platform: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """List scheduled queue for current organization with account and platform metadata."""
    query = db.query(ScheduledPost).outerjoin(
        Content, ScheduledPost.content_id == Content.id
    ).outerjoin(
        BrandProfile, Content.brand_profile_id == BrandProfile.id
    ).filter(
        or_(
            ScheduledPost.organization_id == current_user.organization_id,
            BrandProfile.organization_id == current_user.organization_id
        )
    )
    eff_status = (status or status_filter or "").strip().lower()
    if eff_status and eff_status != "all":
        query = query.filter(ScheduledPost.status.ilike(eff_status))
    if platform and platform != "all":
        query = query.filter(ScheduledPost.platform.ilike(f"%{platform}%"))
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(or_(
            ScheduledPost.title.ilike(term),
            ScheduledPost.caption.ilike(term),
            Content.title.ilike(term),
            Content.caption.ilike(term)
        ))
    posts = query.order_by(ScheduledPost.scheduled_time.desc()).all()
    return [_format_scheduled_response(p) for p in posts]


@router.get("/posts", response_model=List[ScheduledPostResponse])
def list_scheduled_posts(
    status: Optional[str] = None,
    status_filter: Optional[str] = None,
    platform: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """List all scheduled posts for current organization."""
    return get_scheduled_queue(status, status_filter, platform, search, db, current_user)


@router.get("/calendar")
def get_calendar_events(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    platform: Optional[str] = None,
    collection_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Retrieve scheduled and published events formatted for Content Calendar view."""
    query = db.query(ScheduledPost).join(Content, ScheduledPost.content_id == Content.id).join(
        BrandProfile, Content.brand_profile_id == BrandProfile.id
    ).filter(BrandProfile.organization_id == current_user.organization_id)

    if platform and platform != "all":
        query = query.join(SocialAccount, ScheduledPost.social_account_id == SocialAccount.id).filter(
            SocialAccount.platform.ilike(f"%{platform}%")
        )

    if start_date:
        try:
            st_dt = datetime.fromisoformat(start_date)
            query = query.filter(ScheduledPost.scheduled_time >= st_dt)
        except Exception:
            pass

    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date)
            query = query.filter(ScheduledPost.scheduled_time <= end_dt)
        except Exception:
            pass

    posts = query.order_by(ScheduledPost.scheduled_time.asc()).all()

    events = []
    for p in posts:
        content_title = (p.content.title if p.content else None) or (p.content.caption[:40] if p.content and p.content.caption else "Scheduled Post")
        meta = (p.content.meta_info if p.content and isinstance(p.content.meta_info, dict) else {}) or {}
        media_url = meta.get("mediaUrl") or meta.get("thumbnailUrl")
        platform_name = p.social_account.platform if p.social_account else "instagram"
        account_name = p.social_account.account_name if p.social_account else "Social Account"

        start_iso = p.scheduled_time.isoformat() if p.scheduled_time else datetime.utcnow().isoformat()
        if not start_iso.endswith("Z") and "+" not in start_iso:
            start_iso += "Z"

        events.append({
            "id": p.id,
            "title": content_title,
            "start": start_iso,
            "end": start_iso,
            "status": p.status.value.lower() if hasattr(p.status, "value") else str(p.status).lower(),
            "platform": platform_name,
            "account_name": account_name,
            "media_url": media_url,
            "timezone": p.timezone or "Asia/Kolkata",
            "content_id": p.content_id,
            "raw_post": _format_scheduled_response(p).dict()
        })

    return events



@router.post("", status_code=status.HTTP_201_CREATED, include_in_schema=False)
@router.post("/", status_code=status.HTTP_201_CREATED, include_in_schema=False)
@router.post("/posts", status_code=status.HTTP_201_CREATED)
async def create_post(
    payload: ScheduledPostCreate,
    publish_now: bool = Query(False, description="Publish immediately instead of waiting for scheduled time"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Schedule content for automatic publishing across selected or all connected social media accounts."""
    if payload.content_id:
        content = db.query(Content).join(BrandProfile, Content.brand_profile_id == BrandProfile.id).filter(
            Content.id == payload.content_id,
            BrandProfile.organization_id == current_user.organization_id
        ).first()
        if not content:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found or unauthorized.")
    else:
        media_asset = None
        if payload.media_asset_id:
            media_asset = db.query(MediaAsset).filter(
                MediaAsset.id == payload.media_asset_id,
                MediaAsset.organization_id == current_user.organization_id
            ).first()
            if not media_asset:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media asset not found or unauthorized.")

        brand = db.query(BrandProfile).filter(
            BrandProfile.organization_id == current_user.organization_id
        ).first()
        if not brand:
            brand = BrandProfile(
                organization_id=current_user.organization_id,
                name="Default Studio",
                industry="Creative & Media",
                tone_of_voice="Engaging",
                key_themes=[],
                hashtags=[],
                color_palette=[]
            )
            db.add(brand)
            db.flush()

        content = Content(
            title=payload.title or "Untitled Post",
            caption=payload.caption or "",
            hashtags=payload.hashtags or [],
            target_platforms=[],
            status=ContentStatus.DRAFT,
            meta_info={
                "internal_tags": payload.internal_tags or [],
                "post_format": payload.post_format,
                "mediaUrl": media_asset.file_url if media_asset else None,
                "media_type": media_asset.media_type.value if media_asset and hasattr(media_asset.media_type, "value") else None
            },
            brand_profile_id=brand.id,
            creator_id=current_user.id,
            media_asset_id=media_asset.id if media_asset else None
        )
        db.add(content)
        db.flush()

    # A scheduled job runs later and social providers must fetch the media
    # without authentication. Do not queue local /uploads URLs; only durable
    # Cloudinary URLs are publishable.
    if content.media_asset and content.media_asset.file_url:
        media_url = content.media_asset.file_url
    else:
        media_url = (content.meta_info or {}).get("mediaUrl") or (content.meta_info or {}).get("media_url")
    if media_url and (not media_url.startswith("https://") or "res.cloudinary.com" not in media_url):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Upload the image or video to Cloudinary before scheduling it. Local upload URLs cannot be published later."
        )

    target_accounts: List[SocialAccount] = []

    # 1. If explicit list of social account IDs provided:
    if payload.social_account_ids and len(payload.social_account_ids) > 0:
        requested_accounts = db.query(SocialAccount).join(
            BrandProfile, SocialAccount.brand_profile_id == BrandProfile.id
        ).filter(
            SocialAccount.id.in_(payload.social_account_ids),
            BrandProfile.organization_id == current_user.organization_id
        ).all()

        for acc in requested_accounts:
            if not acc.is_connected:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Selected account '{acc.account_name}' ({acc.platform}) is not active or verified. Please reconnect it first."
                )
            target_accounts.append(acc)

    # 2. If single social account ID provided:
    elif payload.social_account_id:
        single_acc = db.query(SocialAccount).join(
            BrandProfile, SocialAccount.brand_profile_id == BrandProfile.id
        ).filter(
            SocialAccount.id == payload.social_account_id,
            BrandProfile.organization_id == current_user.organization_id
        ).first()
        if not single_acc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target social account not found.")
        if not single_acc.is_connected:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Selected account '{single_acc.account_name}' ({single_acc.platform}) is disconnected. Please verify credentials."
            )
        target_accounts.append(single_acc)

    # 3. Default (or post_to_all): all connected accounts for brand / organization
    else:
        all_connected = db.query(SocialAccount).join(
            BrandProfile, SocialAccount.brand_profile_id == BrandProfile.id
        ).filter(
            BrandProfile.organization_id == current_user.organization_id,
            SocialAccount.is_connected == True
        ).all()
        if not all_connected:
            all_connected = db.query(SocialAccount).filter(
                SocialAccount.brand_profile_id == content.brand_profile_id,
                SocialAccount.is_connected == True
            ).all()

        # Deduplicate by platform
        seen_plats = set()
        for acc in all_connected:
            plat_k = str(acc.platform).lower()
            if plat_k not in seen_plats:
                seen_plats.add(plat_k)
                target_accounts.append(acc)

    if not target_accounts and publish_now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No connected social media accounts found. Please connect your social media accounts first to publish immediately."
        )

    # Normalize scheduled_time to naive UTC
    st = payload.scheduled_time
    if st.tzinfo is not None:
        st = st.astimezone(timezone.utc).replace(tzinfo=None)

    created_posts = []
    if target_accounts:
        for acc in target_accounts:
            post = ScheduledPost(
                title=content.title,
                caption=content.caption,
                hashtags=content.hashtags or [],
                internal_tags=payload.internal_tags or [],
                platform=acc.platform.value if hasattr(acc.platform, "value") else str(acc.platform),
                post_format=payload.post_format,
                content_id=content.id,
                social_account_id=acc.id,
                media_asset_id=content.media_asset_id,
                scheduled_time=st,
                timezone=payload.timezone,
                status=ContentStatus.SCHEDULED,
                organization_id=current_user.organization_id,
                user_id=current_user.id,
                publish_metadata={
                    "target_platform": acc.platform,
                    "target_account_name": acc.account_name
                }
            )
            db.add(post)
            created_posts.append(post)
    else:
        post = ScheduledPost(
            title=content.title,
            caption=content.caption,
            hashtags=content.hashtags or [],
            internal_tags=payload.internal_tags or [],
            platform=payload.platform or "instagram",
            post_format=payload.post_format,
            content_id=content.id,
            social_account_id=None,
            media_asset_id=content.media_asset_id,
            scheduled_time=st,
            timezone=payload.timezone,
            status=ContentStatus.SCHEDULED,
            organization_id=current_user.organization_id,
            user_id=current_user.id,
            publish_metadata={
                "target_platform": payload.platform or "instagram"
            }
        )
        db.add(post)
        created_posts.append(post)

    content.status = ContentStatus.SCHEDULED
    db.commit()
    for p in created_posts:
        db.refresh(p)

    if publish_now:
        publish_results = []
        for post in created_posts:
            result = await PublishingWorker.publish_single_post(db, post.id, is_manual=True)
            publish_results.append({
                "account": post.social_account.account_name if post.social_account else "Social Account",
                "platform": post.social_account.platform if post.social_account else payload.platform,
                "success": result.get("success", False),
                "status": "published" if result.get("success") else "failed",
                "error": result.get("error")
            })
        db.refresh(created_posts[0])
        response = _format_scheduled_response(created_posts[0])
        response.publish_metadata = {**(response.publish_metadata or {}), "results": publish_results}
        return response

    return _format_scheduled_response(created_posts[0])


@router.post("/{post_id}/reschedule", response_model=ScheduledPostResponse, include_in_schema=False)
@router.put("/{post_id}", response_model=ScheduledPostResponse)
@router.put("/posts/{post_id}", response_model=ScheduledPostResponse)
def update_scheduled_post(
    post_id: int,
    payload: ScheduledPostUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Edit scheduled publishing time, timezone, target account, or status."""
    post = db.query(ScheduledPost).join(Content, ScheduledPost.content_id == Content.id).join(
        BrandProfile, Content.brand_profile_id == BrandProfile.id
    ).filter(
        ScheduledPost.id == post_id,
        BrandProfile.organization_id == current_user.organization_id
    ).first()
    # Older calendar payloads used the content ID instead of the scheduled
    # row ID. Accept that identifier as a safe, organization-scoped fallback.
    if not post:
        post = db.query(ScheduledPost).join(Content, ScheduledPost.content_id == Content.id).join(
            BrandProfile, Content.brand_profile_id == BrandProfile.id
        ).filter(
            ScheduledPost.content_id == post_id,
            BrandProfile.organization_id == current_user.organization_id,
            ScheduledPost.status == ContentStatus.SCHEDULED
        ).order_by(ScheduledPost.id.asc()).first()
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scheduled post not found or unauthorized.")

    if payload.scheduled_time is not None:
        st = payload.scheduled_time
        if st.tzinfo is not None:
            st = st.astimezone(timezone.utc).replace(tzinfo=None)
        post.scheduled_time = st
    if payload.timezone is not None:
        post.timezone = payload.timezone
    if payload.status is not None:
        post.status = payload.status
    if payload.social_account_id is not None:
        target_acc = db.query(SocialAccount).filter(SocialAccount.id == payload.social_account_id).first()
        if target_acc and target_acc.is_connected:
            post.social_account_id = target_acc.id

    db.commit()
    db.refresh(post)
    return _format_scheduled_response(post)


@router.get("/optimal-slots")
def get_optimal_slots(
    platform: Optional[str] = "instagram",
    count: int = 4,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get real-time AI recommended timeslots and armed scheduled posts across channels."""
    now_utc = datetime.utcnow()
    ist_now = now_utc + timedelta(hours=5, minutes=30)
    platforms_list = ["instagram", "facebook", "youtube", "twitter"]

    connected = db.query(SocialAccount).join(
        BrandProfile, SocialAccount.brand_profile_id == BrandProfile.id
    ).filter(
        BrandProfile.organization_id == current_user.organization_id,
        SocialAccount.is_connected == True
    ).all()
    connected_platforms = {str(a.platform).lower(): a.account_name for a in connected}

    channel_meta = {
        "instagram": {"channel": "Instagram Feed & Reels", "badge": "Highest Reach", "color": "#E1306C"},
        "facebook": {"channel": "Facebook Store Page", "badge": "High Engagement", "color": "#1877F2"},
        "youtube": {"channel": "YouTube Shorts", "badge": "High Retention", "color": "#FF0000"},
        "twitter": {"channel": "Twitter / X Feed", "badge": "Live Updates", "color": "#0f172a"}
    }

    peak_hours_ist = {
        "instagram": [12, 17, 20],
        "facebook": [13, 16, 19],
        "youtube": [15, 18, 21],
        "twitter": [10, 14, 18]
    }

    def _get_next_open_slots(plat_key: str, booked_dt_list: list, num_slots: int = 2):
        hours = peak_hours_ist.get(plat_key, [12, 16, 19])
        results = []
        check_day = ist_now.date()
        for day_offset in range(7):
            cur_date = check_day + timedelta(days=day_offset)
            for h in hours:
                slot_dt = datetime(cur_date.year, cur_date.month, cur_date.day, h, 0)
                if slot_dt > ist_now:
                    collision = any(abs((slot_dt - b).total_seconds()) < 3600 for b in booked_dt_list)
                    if not collision:
                        day_pfx = "Today" if day_offset == 0 else ("Tomorrow" if day_offset == 1 else slot_dt.strftime("%d %b"))
                        results.append(f"{day_pfx}, {slot_dt.strftime('%I:%M %p IST')}")
                        if len(results) >= num_slots:
                            return results
        return results or ["Today, 06:00 PM IST", "Tomorrow, 12:00 PM IST"]

    channels_data = []
    for p in platforms_list:
        meta = channel_meta.get(p, {"channel": p.title(), "badge": "Active", "color": "#59171b"})

        # Query all scheduled posts armed for this platform
        scheduled_posts = db.query(ScheduledPost).join(
            SocialAccount, ScheduledPost.social_account_id == SocialAccount.id
        ).outerjoin(
            Content, ScheduledPost.content_id == Content.id
        ).outerjoin(
            BrandProfile, or_(Content.brand_profile_id == BrandProfile.id, SocialAccount.brand_profile_id == BrandProfile.id)
        ).filter(
            or_(
                ScheduledPost.organization_id == current_user.organization_id,
                BrandProfile.organization_id == current_user.organization_id
            ),
            ScheduledPost.status == "scheduled",
            func.lower(SocialAccount.platform) == p
        ).order_by(ScheduledPost.scheduled_time.asc()).all()

        booked_ist_list = []
        for sp in scheduled_posts:
            if sp.scheduled_time:
                booked_ist_list.append(sp.scheduled_time + timedelta(hours=5, minutes=30))

        has_scheduled = len(scheduled_posts) > 0
        armed_post = None

        if has_scheduled:
            first_sp = scheduled_posts[0]
            sp_ist = first_sp.scheduled_time + timedelta(hours=5, minutes=30) if first_sp.scheduled_time else ist_now
            is_today = sp_ist.date() == ist_now.date()
            is_tomorrow = sp_ist.date() == (ist_now + timedelta(days=1)).date()
            day_prefix = "Today" if is_today else ("Tomorrow" if is_tomorrow else sp_ist.strftime("%d %b"))
            armed_time_display = f"{day_prefix}, {sp_ist.strftime('%I:%M %p IST')}"
            armed_title = first_sp.content.title if first_sp.content and first_sp.content.title else f"Post #{first_sp.content_id}"

            armed_post = {
                "id": first_sp.id,
                "content_id": first_sp.content_id,
                "title": armed_title,
                "scheduled_time": first_sp.scheduled_time.isoformat() if first_sp.scheduled_time else None,
                "scheduled_time_display": armed_time_display,
                "status": "scheduled"
            }

            slot1_label = "Armed Scheduled Post"
            slot1_str = armed_time_display
            slot1_status = "booked"
            slot1_title = armed_title

            # Window 2 is the next open AI slot after the booked post
            open_slots = _get_next_open_slots(p, booked_ist_list, 1)
            slot2_label = "Next Open AI Window"
            slot2_str = f"{open_slots[0]} (Peak)" if open_slots else "Tomorrow, 12:00 PM IST (Peak)"
            slot2_status = "recommended"
        else:
            open_slots = _get_next_open_slots(p, booked_ist_list, 2)
            slot1_label = "Optimal Window 1"
            slot1_str = open_slots[0] if len(open_slots) > 0 else "Today, 12:00 PM IST"
            slot1_status = "open"
            slot1_title = None

            slot2_label = "Optimal Window 2"
            slot2_str = f"{open_slots[1]} (Peak)" if len(open_slots) > 1 else "Tomorrow, 06:00 PM IST (Peak)"
            slot2_status = "recommended"

        channels_data.append({
            "platform": p,
            "channel": meta["channel"],
            "badge": meta["badge"],
            "color": meta["color"],
            "has_scheduled": has_scheduled,
            "armed_post": armed_post,
            "slot1_label": slot1_label,
            "slot1": slot1_str,
            "slot1_status": slot1_status,
            "slot1_title": slot1_title,
            "slot2_label": slot2_label,
            "slot2": slot2_str,
            "slot2_status": slot2_status,
            "is_connected": p in connected_platforms,
            "account_name": connected_platforms.get(p)
        })

    target_plat = (platform or "instagram").lower()
    return {
        "channels": channels_data,
        "slots": [s.isoformat() for s in ScheduleManager.get_optimal_slots(target_plat, now_utc, count)]
    }


@router.get("/posts/{post_id}", response_model=ScheduledPostResponse)
def get_scheduled_post_by_id(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Retrieve single scheduled post by ID with full metadata."""
    post = db.query(ScheduledPost).outerjoin(
        Content, ScheduledPost.content_id == Content.id
    ).outerjoin(
        BrandProfile, Content.brand_profile_id == BrandProfile.id
    ).filter(
        ScheduledPost.id == post_id,
        or_(
            ScheduledPost.organization_id == current_user.organization_id,
            BrandProfile.organization_id == current_user.organization_id
        )
    ).first()
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scheduled post not found.")
    return _format_scheduled_response(post)


@router.post("/posts/{post_id}/publish")
async def publish_scheduled_post_now(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Immediately dispatch a scheduled post in real-time."""
    post = db.query(ScheduledPost).outerjoin(
        Content, ScheduledPost.content_id == Content.id
    ).outerjoin(
        BrandProfile, Content.brand_profile_id == BrandProfile.id
    ).filter(
        ScheduledPost.id == post_id,
        or_(
            ScheduledPost.organization_id == current_user.organization_id,
            BrandProfile.organization_id == current_user.organization_id
        )
    ).first()
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scheduled post not found.")

    post.status = "scheduled"
    post.error_message = None
    db.commit()

    result = await PublishingWorker.publish_single_post(db, post.id, is_manual=True)
    db.refresh(post)
    return {
        "success": result.get("success", False),
        "status": post.status,
        "error": result.get("error"),
        "message": "Post published live successfully!" if result.get("success") else result.get("error", "Publishing failed"),
        "post": _format_scheduled_response(post)
    }


@router.post("/posts/{post_id}/cancel")
def cancel_scheduled_post_endpoint(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Cancel a scheduled post and return its content to approved."""
    post = db.query(ScheduledPost).outerjoin(
        Content, ScheduledPost.content_id == Content.id
    ).outerjoin(
        BrandProfile, Content.brand_profile_id == BrandProfile.id
    ).filter(
        ScheduledPost.id == post_id,
        or_(
            ScheduledPost.organization_id == current_user.organization_id,
            BrandProfile.organization_id == current_user.organization_id
        )
    ).first()
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scheduled post not found.")

    post.status = "cancelled"
    content_id = post.content_id
    db.commit()

    if content_id:
        remaining = db.query(ScheduledPost).filter(
            ScheduledPost.content_id == content_id,
            ScheduledPost.status == ContentStatus.SCHEDULED
        ).count()
        if remaining == 0:
            content = db.query(Content).filter(Content.id == content_id).first()
            if content and content.status == ContentStatus.SCHEDULED:
                content.status = ContentStatus.APPROVED
                db.commit()

    return {"success": True, "message": "Post schedule cancelled.", "post_id": post_id}


@router.delete("/posts/{post_id}", status_code=status.HTTP_200_OK)
@router.delete("/{post_id}", status_code=status.HTTP_200_OK)
def delete_scheduled_post(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Remove or cancel a scheduled post item."""
    post = db.query(ScheduledPost).outerjoin(
        Content, ScheduledPost.content_id == Content.id
    ).outerjoin(
        BrandProfile, Content.brand_profile_id == BrandProfile.id
    ).filter(
        ScheduledPost.id == post_id,
        or_(
            ScheduledPost.organization_id == current_user.organization_id,
            BrandProfile.organization_id == current_user.organization_id
        )
    ).first()
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scheduled post not found or unauthorized.")

    content_id = post.content_id
    db.delete(post)
    db.commit()

    if content_id:
        remaining = db.query(ScheduledPost).filter(
            ScheduledPost.content_id == content_id,
            ScheduledPost.status == ContentStatus.SCHEDULED
        ).count()
        if remaining == 0:
            content = db.query(Content).filter(Content.id == content_id).first()
            if content and content.status == ContentStatus.SCHEDULED:
                content.status = ContentStatus.APPROVED
                db.commit()

    return {"success": True, "message": "Scheduled post removed successfully."}


@router.patch("/posts/{post_id}", response_model=ScheduledPostResponse)
def patch_scheduled_post(
    post_id: int,
    payload: ScheduledPostUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Patch scheduled post attributes."""
    return update_scheduled_post(post_id, payload, db, current_user)


@router.post("/posts/{post_id}/retry")
async def retry_scheduled_post(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """Manually retry publishing a failed scheduled post immediately."""
    post = db.query(ScheduledPost).outerjoin(
        Content, ScheduledPost.content_id == Content.id
    ).outerjoin(
        BrandProfile, Content.brand_profile_id == BrandProfile.id
    ).filter(
        ScheduledPost.id == post_id,
        or_(
            ScheduledPost.organization_id == current_user.organization_id,
            BrandProfile.organization_id == current_user.organization_id
        )
    ).first()
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scheduled post not found or unauthorized.")

    post.status = "scheduled"
    post.error_message = None
    db.commit()

    result = await PublishingWorker.publish_single_post(db, post.id, is_manual=True)
    db.refresh(post)
    if not result.get("success"):
        return {"success": False, "error": result.get("error", "Publishing failed"), "post": _format_scheduled_response(post)}
    return {"success": True, "result": result, "message": "Post published successfully!", "post": _format_scheduled_response(post)}
