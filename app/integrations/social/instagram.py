from datetime import datetime
import os
import asyncio
from typing import Dict, Any, List, Optional, Tuple
import httpx
from app.integrations.social.base import BaseSocialClient
from app.core.logging import logger
from app.core.security import decrypt_token


class InstagramClient(BaseSocialClient):
    """Instagram Graph API integration client supporting both Instagram Graph API and Meta Facebook API."""

    def __init__(self, access_token: str | None = None):
        super().__init__(access_token=access_token)
        self.decrypted_token = decrypt_token(self.access_token) if self.access_token else ""

    def _get_api_bases(self) -> List[str]:
        """Returns list of API base URLs to try based on token format."""
        token = self.decrypted_token or ""
        if token.startswith("IG"):
            return ["https://graph.instagram.com/v19.0", "https://graph.facebook.com/v19.0"]
        return ["https://graph.facebook.com/v19.0", "https://graph.instagram.com/v19.0"]

    async def _resolve_account_id(self, client: httpx.AsyncClient, account_id: Optional[str] = None) -> Tuple[str, str]:
        """
        Resolves the numerical Instagram Business Account ID and base URL.
        Returns (resolved_numeric_id, base_url).
        """
        token = self.decrypted_token
        bases = self._get_api_bases()

        # If token starts with IG (direct Instagram Basic/Graph API)
        if token.startswith("IG"):
            try:
                res = await client.get(f"https://graph.instagram.com/v19.0/me?fields=id,username&access_token={token}")
                if res.status_code == 200:
                    data = res.json()
                    real_id = data.get("id")
                    if real_id:
                        logger.info(f"Resolved Instagram numeric account ID: {real_id} (@{data.get('username')}) via graph.instagram.com")
                        return str(real_id), "https://graph.instagram.com/v19.0"
            except Exception as e:
                logger.debug(f"Failed querying graph.instagram.com/me: {e}")

        # If token starts with EAA (Meta/Facebook token), find linked instagram_business_account
        if token.startswith("EAA"):
            try:
                res = await client.get(
                    f"https://graph.facebook.com/v19.0/me/accounts?fields=id,name,instagram_business_account{{id,username}}&access_token={token}"
                )
                if res.status_code == 200:
                    pages = res.json().get("data", [])
                    for page in pages:
                        ig = page.get("instagram_business_account")
                        if ig and ig.get("id"):
                            logger.info(f"Resolved linked Instagram Business ID: {ig['id']} (@{ig.get('username')}) via Page {page.get('id')}")
                            return str(ig["id"]), "https://graph.facebook.com/v19.0"
            except Exception as e:
                logger.debug(f"Failed querying me/accounts: {e}")

        # If already numeric and not 'me', verify or use
        if account_id and str(account_id).isdigit() and account_id != "me":
            return str(account_id), bases[0]

        # Query /me to discover the real numeric ID
        for base in bases:
            try:
                res = await client.get(f"{base}/me?fields=id,username&access_token={token}")
                if res.status_code == 200:
                    data = res.json()
                    real_id = data.get("id")
                    if real_id:
                        logger.info(f"Resolved Instagram numeric account ID: {real_id} (@{data.get('username')}) via {base}")
                        return str(real_id), base
            except Exception as e:
                logger.debug(f"Failed querying {base}/me: {e}")

        # Fallback to provided account_id or me
        return str(account_id or "me"), bases[0]

    def _ensure_public_media_url(self, media_urls: Optional[List[str]], prefer_video: bool = False) -> str:
        """
        Ensures a publicly accessible HTTPS media URL is returned.
        Instagram's media fetcher must be able to retrieve the asset without
        authentication; local URLs are not publishable.
        """
        candidate = ""
        if media_urls and len(media_urls) > 0:
            ordered_urls = list(media_urls)
            if prefer_video:
                ordered_urls.sort(key=lambda u: 0 if any(ext in str(u).lower() for ext in (".mp4", ".mov", ".webm", "/video/")) else 1)
            for u in ordered_urls:
                if u and isinstance(u, str) and (u.startswith("http://") or u.startswith("https://")):
                    if "localhost" not in u and "127.0.0.1" not in u:
                        candidate = u
                        break

        # If no public URL is found, try uploading a supplied local file to the configured storage provider.
        if not candidate:
            # Check local uploads directory for any image
            try:
                from app.integrations.storage.cloudinary_storage import CloudinaryStorageProvider
                import cloudinary.uploader
                CloudinaryStorageProvider._init_cloudinary()

                # Try local media path if supplied
                if media_urls and len(media_urls) > 0 and os.path.exists(str(media_urls[0])):
                    res = cloudinary.uploader.upload(str(media_urls[0]), folder="instagram_posts")
                    candidate = res.get("secure_url") or res.get("url")
            except Exception as e:
                logger.warning(f"Cloudinary upload fallback error: {e}")

        if not candidate:
            raise ValueError(
                "Instagram requires a publicly reachable HTTPS media URL. Upload the selected asset to configured object storage before publishing."
            )

        return candidate

    async def publish_post(self, content_text: str, media_urls: List[str] = None, **kwargs) -> Dict[str, Any]:
        """
        Publishes an image/video post with caption to Instagram Graph API.
        Automatically resolves Instagram Business Account ID and creates/publishes media container.
        """
        logger.info(f"Publishing to Instagram: caption='{(content_text or '')[:30]}...' media_count={len(media_urls or [])}")

        token = self.decrypted_token
        if not token:
            raise ValueError(
                "Instagram publishing requires an active OAuth 2.0 Access Token with 'instagram_content_publish' permission. "
                "Please connect your Instagram Professional account."
            )

        provided_account_id = kwargs.get("account_id")
        raw_format = str(kwargs.get("post_format") or kwargs.get("format") or "post").lower()
        if "reel" in raw_format or "short" in raw_format:
            post_format = "reel"
        elif "story" in raw_format or "stories" in raw_format:
            post_format = "story"
        else:
            post_format = "post"

        declared_media_type = str(kwargs.get("media_type") or "").lower()
        prefer_video = post_format in {"reel", "reels", "short", "shorts"} or declared_media_type in {"video", "mediatype.video"}
        media_url = self._ensure_public_media_url(media_urls, prefer_video=prefer_video)
        is_video = declared_media_type == "video" or media_url.lower().split("?")[0].endswith((".mp4", ".mov", ".webm")) or "/video/" in media_url.lower()

        # Modern Instagram publishes all video feed content as Reels with feed distribution
        if is_video and post_format in {"post", "feed", "photo", ""}:
            post_format = "reel"
        # Standardize video to strict Instagram 9:16 portrait Reel format (1080x1920) if needed
        if is_video and media_url:
            try:
                from app.services.video_processing_service import VideoProcessingService
                media_url, _ = VideoProcessingService.ensure_video_ready_for_platform(
                    media_url_or_path=media_url,
                    platform="instagram",
                    post_format=post_format
                )
            except Exception as v_err:
                logger.warning(f"Instagram pre-publish video standardization notice: {v_err}")

        if post_format in {"reel", "reels", "short", "shorts"} and not is_video:
            raise ValueError("Instagram Reels require a video asset.")
        if post_format == "story" and not media_url:
            raise ValueError("Instagram Stories require an image or video asset.")
        if post_format not in {"post", "feed", "reel", "reels", "short", "shorts", "story"}:
            raise ValueError(f"Instagram does not support post format '{post_format}'.")

        async with httpx.AsyncClient(timeout=45.0) as client:
            account_id, base_url = await self._resolve_account_id(client, provided_account_id)
            
            # Step 1: Create media container
            container_endpoint = f"{base_url}/{account_id}/media"
            payload = {"access_token": token}
            if post_format in {"reel", "reels", "short", "shorts"}:
                payload.update({
                    "media_type": "REELS",
                    "video_url": media_url,
                    "caption": content_text or "",
                    "share_to_feed": "true"
                })
            elif post_format == "story":
                payload["media_type"] = "STORIES"
                payload["video_url" if is_video else "image_url"] = media_url
            else:
                payload.update({"caption": content_text or "", "image_url": media_url})

            logger.info(f"Posting {post_format} container to {container_endpoint} (media_url={media_url[:50]}...)")
            res = await client.post(container_endpoint, data=payload)

            # If failed, attempt alternative base URL
            if res.status_code != 200:
                alt_base = "https://graph.instagram.com/v19.0" if "facebook" in base_url else "https://graph.facebook.com/v19.0"
                logger.warning(f"Container creation failed at {base_url} (HTTP {res.status_code}: {res.text}). Retrying with {alt_base}...")
                alt_endpoint = f"{alt_base}/{account_id}/media"
                alt_res = await client.post(alt_endpoint, data=payload)
                if alt_res.status_code == 200:
                    res = alt_res
                    base_url = alt_base
                else:
                    err_msg = res.text
                    try:
                        err_json = res.json()
                        err_msg = err_json.get("error", {}).get("message", res.text)
                    except Exception:
                        pass
                    raise RuntimeError(f"Instagram media container creation failed (HTTP {res.status_code}): {err_msg}")

            creation_data = res.json()
            creation_id = creation_data.get("id")
            if not creation_id:
                raise RuntimeError(f"Instagram container creation response missing id: {creation_data}")

            # Step 1.5: Poll container status until FINISHED (both video and image containers)
            status_endpoint = f"{base_url}/{creation_id}"
            max_status_checks = 45 if is_video else 12  # up to 135s for videos/reels, 24s for images
            container_ready = False
            last_status = "UNKNOWN"

            for attempt in range(max_status_checks):
                try:
                    st_res = await client.get(
                        status_endpoint,
                        params={"fields": "status_code,status", "access_token": token}
                    )
                    if st_res.status_code == 200:
                        st_data = st_res.json()
                        status_code = st_data.get("status_code")
                        last_status = status_code or st_data.get("status") or "UNKNOWN"
                        logger.info(
                            f"Instagram container {creation_id} status: {last_status} "
                            f"(check {attempt + 1}/{max_status_checks})"
                        )

                        if status_code == "FINISHED":
                            container_ready = True
                            # Allow Meta's distributed clusters to synchronize the ready state
                            await asyncio.sleep(2.0)
                            break
                        elif status_code in ["ERROR", "EXPIRED"]:
                            err_detail = st_data.get("status") or f"Container returned {status_code}"
                            raise RuntimeError(
                                f"Instagram media processing failed for container {creation_id}: {err_detail}"
                            )
                    elif st_res.status_code in [400, 404]:
                        # Static image containers occasionally do not expose status endpoint
                        if not is_video and attempt >= 2:
                            container_ready = True
                            break
                except RuntimeError:
                    raise
                except Exception as e:
                    logger.debug(f"Container status check attempt error: {e}")

                await asyncio.sleep(3.0)

            if not container_ready and is_video:
                logger.warning(
                    f"Instagram container {creation_id} status is still '{last_status}' after {max_status_checks * 3}s. "
                    "Entering publish retry loop with extended backoff..."
                )

            # Step 2: Publish the media container (with extended retry for transient 2207027 'Media ID is not available')
            pub_endpoint = f"{base_url}/{account_id}/media_publish"
            logger.info(f"Publishing container {creation_id} at {pub_endpoint} (last container status: {last_status})...")

            pub_res = None
            max_pub_attempts = 10  # 10 attempts * 3s = 30s retry window
            for pub_attempt in range(max_pub_attempts):
                pub_res = await client.post(
                    pub_endpoint,
                    data={"creation_id": creation_id, "access_token": token}
                )
                if pub_res.status_code == 200:
                    break

                err_text = pub_res.text
                is_transient = (
                    "2207027" in err_text
                    or "not available" in err_text.lower()
                    or "not ready" in err_text.lower()
                    or "media is being processed" in err_text.lower()
                )
                if is_transient and pub_attempt < max_pub_attempts - 1:
                    logger.info(
                        f"Media container {creation_id} still processing/propagating on Instagram. "
                        f"Retrying publish in 3s (attempt {pub_attempt + 1}/{max_pub_attempts})..."
                    )
                    await asyncio.sleep(3.0)
                else:
                    break

            if not pub_res or pub_res.status_code != 200:
                err_text = pub_res.text if pub_res else "no response"
                err_json = {}
                try:
                    err_json = pub_res.json() if pub_res else {}
                    err_text = err_json.get("error", {}).get("message", err_text)
                except Exception:
                    pass
                if "2207051" in str(err_json) or "2207051" in str(err_text):
                    raise RuntimeError(
                        "Instagram Action Blocked (Subcode 2207051): Instagram has placed a temporary security hold "
                        "on automated publishing for this account due to rapid consecutive attempts. "
                        "This temporary security restriction automatically resets in 1 to 2 hours. "
                        "Please pause automated publishing and try again shortly."
                    )
                if "application request limit reached" in err_text.lower() or "user request limit reached" in err_text.lower():
                    raise RuntimeError(
                        "Meta API rate limit reached on Instagram. In Meta Developer mode, Instagram restricts API requests "
                        "to a temporary quota. Please wait a few minutes before retrying, or verify your Meta Developer App status."
                    )
                if "2207027" in str(err_json) or "not available" in err_text.lower():
                    raise RuntimeError(
                        f"Instagram media processing timed out on Meta servers (Subcode 2207027: Media ID is not available). "
                        f"Instagram took longer than expected to download/transcode the media container {creation_id}. "
                        "Please verify your media asset is public and try publishing again."
                    )
                raise RuntimeError(f"Instagram media publish failed: {err_text}")

            pub_data = pub_res.json()
            post_id = pub_data.get("id") or creation_id

            logger.info(f"Instagram post published successfully! Live ID: {post_id}")
            return {
                "success": True,
                "status": "published",
                "platform": "instagram",
                "platform_post_id": str(post_id),
                "permalink": f"https://instagram.com/p/{post_id}",
                "media_url": media_url,
                "media_type": "video" if is_video else "image",
                "account_id": account_id,
                "raw_response": pub_data
            }

    def publish(self, social_account=None, content: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Synchronous publish adapter for autonomous pipeline and background workers.
        """
        import asyncio
        caption = ""
        media_urls = []
        account_id = None

        if content:
            caption = content.get("caption") or content.get("title") or ""
            if content.get("media_path"):
                media_urls.append(content["media_path"])
            if content.get("media_urls"):
                media_urls.extend(content["media_urls"])

        if social_account:
            account_id = getattr(social_account, "account_id", None)
            if getattr(social_account, "access_token", None):
                self.access_token = social_account.access_token
                self.decrypted_token = decrypt_token(self.access_token) or self.access_token

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                self.publish_post(
                    content_text=caption,
                    media_urls=media_urls,
                    account_id=account_id,
                    media_type=content.get("media_type") if content else None,
                    # The autonomous publisher marks generated MP4s as reels.
                    # Forward that intent to the Graph API instead of falling
                    # back to a feed-photo container.
                    post_format=(content.get("post_format") if content else None)
                    or ("reel" if content and content.get("media_type") == "video" else "post"),
                )
            )
        finally:
            loop.close()

    async def fetch_analytics(self, account_id: str, since: str = None) -> Dict[str, Any]:
        token = self.decrypted_token
        if not token:
            return {
                "impressions": 0, "reach": 0, "views": 0, "engagements": 0,
                "likes": 0, "comments": 0, "shares": 0, "clicks": 0,
                "engagement_rate": 0.0, "status": "unauthorized",
                "error": "Missing Instagram access token."
            }

        stats = {
            "impressions": 0, "reach": 0, "views": 0, "engagements": 0,
            "likes": 0, "comments": 0, "shares": 0, "clicks": 0,
            "engagement_rate": 0.0, "status": "connected"
        }

        bases = self._get_api_bases()
        for base in bases:
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    # 1. Fetch user insights
                    res = await client.get(
                        f"{base}/{account_id}/insights",
                        params={
                            "metric": "impressions,reach,profile_views",
                            "period": "day",
                            "access_token": token
                        }
                    )
                    if res.status_code == 200:
                        data = res.json().get("data", [])
                        for item in data:
                            name = item.get("name")
                            val = 0
                            values = item.get("values", [])
                            if values:
                                val = sum(int(v.get("value", 0)) for v in values if isinstance(v.get("value"), (int, float)))
                            if name == "impressions":
                                stats["impressions"] = val
                            elif name == "reach":
                                stats["reach"] = val
                            elif name == "profile_views":
                                stats["clicks"] = val
                    else:
                        # Some Graph API versions reject the combined metric
                        # request. Retry profile views alone so clicks are not
                        # lost when impressions/reach are unavailable.
                        clicks_res = await client.get(
                            f"{base}/{account_id}/insights",
                            params={
                                "metric": "profile_views",
                                "period": "day",
                                "access_token": token
                            }
                        )
                        if clicks_res.status_code == 200:
                            for item in clicks_res.json().get("data", []):
                                values = item.get("values") or []
                                stats["clicks"] = sum(
                                    int(v.get("value", 0)) for v in values
                                    if isinstance(v.get("value"), (int, float))
                                )

                    if not stats["clicks"]:
                        clicks_res = await client.get(
                            f"{base}/{account_id}/insights",
                            params={
                                "metric": "profile_views",
                                "period": "day",
                                "access_token": token
                            }
                        )
                        if clicks_res.status_code == 200:
                            for item in clicks_res.json().get("data", []):
                                values = item.get("values") or []
                                stats["clicks"] = sum(
                                    int(v.get("value", 0)) for v in values
                                    if isinstance(v.get("value"), (int, float))
                                )

                    # 2. Fetch media list to tally real likes, comments, and views
                    media_res = await client.get(
                        f"{base}/{account_id}/media",
                        params={
                            "fields": "id,caption,media_type,like_count,comments_count,timestamp,permalink",
                            "limit": "25",
                            "access_token": token
                        }
                    )
                    if media_res.status_code == 200:
                        media_items = media_res.json().get("data", [])
                        total_likes = sum(int(m.get("like_count", 0)) for m in media_items)
                        total_comments = sum(int(m.get("comments_count", 0)) for m in media_items)
                        media_impressions = 0
                        media_reach = 0
                        media_views = 0
                        media_shares = 0
                        media_interactions = 0

                        # Account-level reach/impressions are unavailable for some
                        # Instagram account types/API versions. In that case, read
                        # the verified insights for each published media item.
                        for media in media_items:
                            media_id = media.get("id")
                            if not media_id:
                                continue
                            try:
                                insight_metric = (
                                    "reach,plays,saved,shares,total_interactions"
                                    if str(media.get("media_type", "")).upper() == "VIDEO"
                                    else "reach,saved,shares,total_interactions"
                                )
                                insight_res = await client.get(
                                    f"{base}/{media_id}/insights",
                                    params={
                                        "metric": insight_metric,
                                        "access_token": token
                                    }
                                )
                                if insight_res.status_code != 200:
                                    # Retry independent metrics. A single
                                    # unsupported metric must not hide shares.
                                    insight_res = await client.get(
                                        f"{base}/{media_id}/insights",
                                        params={
                                            "metric": "reach,shares",
                                            "access_token": token
                                        }
                                    )
                                    if insight_res.status_code != 200:
                                        continue
                                for insight in insight_res.json().get("data", []):
                                    name = insight.get("name")
                                    values = insight.get("values") or []
                                    value = values[-1].get("value", 0) if values else insight.get("value", 0)
                                    if not isinstance(value, (int, float)):
                                        continue
                                    value = int(value)
                                    if name == "impressions":
                                        media_impressions += value
                                    elif name == "reach":
                                        media_reach += value
                                    elif name == "plays":
                                        media_views += value
                                    elif name == "shares":
                                        media_shares += value
                                    elif name == "total_interactions":
                                        media_interactions += value
                            except Exception as insight_error:
                                logger.debug(f"Instagram media insight fetch failed for {media_id}: {insight_error}")

                        stats["likes"] = total_likes
                        stats["comments"] = total_comments
                        stats["shares"] = media_shares
                        stats["impressions"] = stats["impressions"] or media_impressions or media_views
                        stats["reach"] = stats["reach"] or media_reach
                        stats["views"] = media_views
                        stats["engagements"] = max(
                            total_likes + total_comments + media_shares,
                            media_interactions
                        )
                        denominator = stats["impressions"] or stats["reach"]
                        if denominator > 0:
                            stats["engagement_rate"] = round((stats["engagements"] / denominator * 100.0), 2)
                        return stats

            except Exception as e:
                logger.debug(f"Instagram insights fetch error via {base}: {e}")

        return stats

    async def fetch_post_analytics(self, media_id: str) -> Dict[str, Any]:
        """
        Fetches live metrics for a published Instagram post, Reel, or Carousel.
        """
        token = self.decrypted_token
        if not token:
            return {"impressions": 0, "reach": 0, "views": 0, "likes": 0, "comments": 0, "shares": 0, "engagement_rate": 0.0}

        bases = self._get_api_bases()
        for base in bases:
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    # 1. Fetch public counters
                    res = await client.get(
                        f"{base}/{media_id}",
                        params={
                            "fields": "id,like_count,comments_count,media_type,permalink,timestamp",
                            "access_token": token
                        }
                    )
                    if res.status_code == 200:
                        m_data = res.json()
                        likes = int(m_data.get("like_count", 0))
                        comments = int(m_data.get("comments_count", 0))
                        permalink = m_data.get("permalink")
                        views = 0
                        reach = 0
                        shares = 0
                        impressions = 0

                        # 2. Try fetching media insights
                        try:
                            m_type = str(m_data.get("media_type", "")).upper()
                            metric_param = "reach,saved,shares,total_interactions"
                            if m_type == "VIDEO":
                                metric_param = "reach,plays,saved,shares,total_interactions"

                            ins_res = await client.get(
                                f"{base}/{media_id}/insights",
                                params={"metric": metric_param, "access_token": token}
                            )
                            if ins_res.status_code == 200:
                                for ins in ins_res.json().get("data", []):
                                    name = ins.get("name")
                                    val = int(ins.get("values", [{}])[0].get("value", 0))
                                    if name in ["plays", "impressions"]:
                                        views = max(views, val)
                                        impressions = max(impressions, val)
                                    elif name == "reach":
                                        reach = val
                                    elif name == "shares":
                                        shares = val
                        except Exception:
                            pass

                        engagements = likes + comments + shares
                        impr_for_rate = impressions or reach or views or (engagements if engagements > 0 else 1)
                        rate = round((engagements / impr_for_rate * 100.0), 2) if impr_for_rate > 0 else 0.0

                        return {
                            "impressions": impressions or views,
                            "reach": reach,
                            "views": views,
                            "likes": likes,
                            "comments": comments,
                            "shares": shares,
                            "engagements": engagements,
                            "engagement_rate": rate,
                            "permalink": permalink,
                            "status": "connected"
                        }
            except Exception as e:
                logger.debug(f"Instagram post analytics error: {e}")

        return {"impressions": 0, "reach": 0, "views": 0, "likes": 0, "comments": 0, "shares": 0, "engagement_rate": 0.0}

    async def verify_credentials(self) -> bool:
        if not self.decrypted_token:
            return False
        try:
            details = await self.verify_and_fetch_details()
            return details.get("valid", False)
        except Exception:
            return False

    async def refresh_access_token(self, client_id: str = None, client_secret: str = None) -> Dict[str, Any]:
        """
        Refreshes or extends an Instagram access token.
        Supports:
        1. Meta Graph API tokens (starting with EAA) for Instagram Professional / Business accounts:
           - Uses Meta Graph API fb_exchange_token when client credentials are provided.
           - Inspects Meta /debug_token to verify validity and compute remaining expiration.
        2. Instagram Basic Display API tokens (starting with IG or legacy format):
           - Uses graph.instagram.com/refresh_access_token.
        """
        token = self.decrypted_token
        if not token:
            raise ValueError("No Instagram access token available to refresh.")

        token = token.strip()

        async with httpx.AsyncClient(timeout=15.0) as client:
            # Case 1: Meta / Facebook OAuth token (EAA...)
            if token.startswith("EAA"):
                # A. Try refreshing / extending via Meta fb_exchange_token
                if client_id and client_secret:
                    try:
                        res = await client.get(
                            "https://graph.facebook.com/v19.0/oauth/access_token",
                            params={
                                "grant_type": "fb_exchange_token",
                                "client_id": client_id,
                                "client_secret": client_secret,
                                "fb_exchange_token": token
                            }
                        )
                        if res.status_code == 200:
                            data = res.json()
                            return {
                                "access_token": data.get("access_token"),
                                "expires_in": data.get("expires_in", 5184000)
                            }
                        else:
                            logger.warning(f"Meta fb_exchange_token response: {res.text}")
                    except Exception as e:
                        logger.warning(f"Meta fb_exchange_token attempt failed: {e}")

                # B. Inspect token validity via Meta debug_token
                try:
                    debug_res = await client.get(
                        "https://graph.facebook.com/debug_token",
                        params={
                            "input_token": token,
                            "access_token": token
                        }
                    )
                    if debug_res.status_code == 200:
                        ddata = debug_res.json().get("data", {})
                        if ddata.get("is_valid"):
                            expires_at = ddata.get("expires_at", 0)
                            now_ts = int(datetime.utcnow().timestamp())
                            expires_in = max(0, expires_at - now_ts) if expires_at > 0 else 5184000
                            return {
                                "access_token": token,
                                "expires_in": expires_in
                            }
                        else:
                            error_msg = ddata.get("error", {}).get("message", "Meta OAuth token is no longer valid.")
                            raise RuntimeError(f"Meta token invalid: {error_msg}")
                except Exception as e:
                    if "Meta token invalid" in str(e):
                        raise
                    logger.warning(f"Meta debug_token check warning: {e}")

                # C. Check /me as fallback
                me_res = await client.get(f"https://graph.facebook.com/v19.0/me?access_token={token}")
                if me_res.status_code == 200:
                    return {
                        "access_token": token,
                        "expires_in": 5184000
                    }
                raise RuntimeError(f"Meta token refresh failed: {me_res.text}")

            # Case 2: Instagram Basic Display API token (IG...)
            res = await client.get(
                "https://graph.instagram.com/refresh_access_token",
                params={
                    "grant_type": "ig_refresh_token",
                    "access_token": token
                }
            )
            if res.status_code == 200:
                data = res.json()
                return {
                    "access_token": data.get("access_token"),
                    "expires_in": data.get("expires_in")
                }
            raise RuntimeError(f"Instagram token refresh failed: {res.text}")

    async def verify_and_fetch_details(self, account_id: str = None) -> Dict[str, Any]:
        """
        Verify Instagram credentials against official Meta / Instagram Graph API
        and retrieve verified numeric account ID and handle details.
        Supports both direct Instagram Graph API (graph.instagram.com)
        and Meta Facebook Graph API (graph.facebook.com) for linked Instagram Business accounts.
        """
        token = self.decrypted_token
        if not token:
            raise ValueError("Instagram verification requires an active Meta / Instagram access token.")

        errors = []

        async with httpx.AsyncClient(timeout=15.0) as client:
            # 1. First attempt: Direct Instagram Basic / Login API (graph.instagram.com)
            try:
                target = account_id if (account_id and account_id != "me") else "me"
                res = await client.get(
                    f"https://graph.instagram.com/v19.0/{target}?fields=id,username,account_type&access_token={token}"
                )
                if res.status_code == 200:
                    data = res.json()
                    ig_id = data.get("id")
                    username = data.get("username")
                    acc_type = data.get("account_type", "BUSINESS")

                    return {
                        "valid": True,
                        "platform": "instagram",
                        "account_id": str(ig_id),
                        "account_name": f"@{username}" if username and not username.startswith("@") else (username or f"ig_{ig_id}"),
                        "display_name": username or f"Instagram {ig_id}",
                        "profile_url": f"https://instagram.com/{username}" if username else "https://instagram.com",
                        "account_type": acc_type,
                        "permissions": ["instagram_basic", "instagram_content_publish", "pages_show_list", "pages_read_engagement"]
                    }
                else:
                    err_json = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}
                    errors.append(err_json.get("error", {}).get("message", res.text))
            except Exception as e:
                errors.append(str(e))

            # 2. Second attempt: Explicit numeric Instagram Account ID on Meta Graph API
            if account_id and account_id != "me":
                try:
                    res = await client.get(
                        f"https://graph.facebook.com/v19.0/{account_id}?fields=id,username,name&access_token={token}"
                    )
                    if res.status_code == 200:
                        data = res.json()
                        ig_id = data.get("id")
                        username = data.get("username")
                        name = data.get("name") or username
                        return {
                            "valid": True,
                            "platform": "instagram",
                            "account_id": str(ig_id),
                            "account_name": f"@{username}" if username and not username.startswith("@") else (username or name),
                            "display_name": name,
                            "profile_url": f"https://instagram.com/{username}" if username else "https://instagram.com",
                            "permissions": ["instagram_basic", "instagram_content_publish", "pages_show_list", "pages_read_engagement"]
                        }
                    else:
                        err_json = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}
                        errors.append(err_json.get("error", {}).get("message", res.text))
                except Exception as e:
                    errors.append(str(e))

            # 3. Third attempt: Discover linked Instagram Business Account via Meta /me/accounts
            try:
                res = await client.get(
                    f"https://graph.facebook.com/v19.0/me/accounts?fields=id,name,instagram_business_account{{id,username,name}}&access_token={token}"
                )
                if res.status_code == 200:
                    pages_data = res.json().get("data", [])
                    for page in pages_data:
                        ig_acc = page.get("instagram_business_account")
                        if ig_acc and ig_acc.get("id"):
                            ig_id = ig_acc["id"]
                            username = ig_acc.get("username")
                            name = ig_acc.get("name") or username or page.get("name")
                            return {
                                "valid": True,
                                "platform": "instagram",
                                "account_id": str(ig_id),
                                "account_name": f"@{username}" if username and not username.startswith("@") else (username or name),
                                "display_name": name,
                                "profile_url": f"https://instagram.com/{username}" if username else "https://instagram.com",
                                "linked_page_id": page.get("id"),
                                "permissions": ["instagram_basic", "instagram_content_publish", "pages_show_list", "pages_read_engagement"]
                            }
                    if pages_data:
                        errors.append("Facebook Pages found, but no linked Instagram Professional/Business account was connected to them.")
                else:
                    err_json = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}
                    err_info = err_json.get("error", {})
                    code = err_info.get("code")
                    subcode = err_info.get("error_subcode")
                    msg = err_info.get("message", res.text)

                    if code in (190, 102):
                        if subcode == 463:
                            raise ValueError("Instagram/Meta Access Token has expired. Please generate a fresh token.")
                        elif subcode == 467:
                            raise ValueError("Instagram/Meta Access Token was revoked or is invalid. Please reconnect your account.")
                        raise ValueError(f"Instagram/Meta Access Token invalid: {msg}")
                    elif code in (10, 200):
                        raise ValueError(f"Missing required Instagram permissions: {msg}. Ensure 'instagram_basic' and 'instagram_content_publish' are granted.")
                    elif code in (4, 17):
                        raise ValueError("Meta Graph API rate limit reached. Please wait a few moments and try again.")
                    errors.append(msg)
            except ValueError:
                raise
            except Exception as e:
                errors.append(str(e))

            # 4. Check if token belongs to a Facebook personal profile without an Instagram business account
            try:
                res = await client.get(f"https://graph.facebook.com/v19.0/me?fields=id,name&access_token={token}")
                if res.status_code == 200:
                    errors.append(
                        "Connected Meta account is a personal user profile with no linked Instagram Professional/Business account. "
                        "Please link an Instagram Professional account to a Facebook Page in Meta Business Suite."
                    )
            except Exception:
                pass

        combined_err = "; ".join([e for e in errors if e]) or "Could not verify credentials with Meta or Instagram API"
        raise ValueError(f"Instagram verification failed: {combined_err}")
