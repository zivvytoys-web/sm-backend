from datetime import datetime
from typing import Dict, Any, List
import httpx
from app.integrations.social.base import BaseSocialClient
from app.core.logging import logger


class FacebookClient(BaseSocialClient):
    """Facebook Pages Graph API integration client."""

    async def publish_post(self, content_text: str, media_urls: List[str] = None, **kwargs) -> Dict[str, Any]:
        logger.info(f"Publishing to Facebook Page: message='{(content_text or '')[:30]}...'")

        if not self.access_token:
            raise ValueError(
                "Facebook publishing requires an active Page Access Token with 'pages_manage_posts' permission. "
                "Please connect your Facebook Page with valid credentials."
            )

        page_id = str(kwargs.get("page_id", "me") or "me")
        urls = [u for u in (media_urls or []) if isinstance(u, str) and u]
        media_type = str(kwargs.get("media_type") or "").lower()
        video_url = next((u for u in urls if ".mp4" in u.lower() or ".mov" in u.lower() or ".webm" in u.lower() or "/video/" in u.lower()), None)
        image_url = next((u for u in urls if u != video_url), None)
        async with httpx.AsyncClient(timeout=30.0) as client:
            target_page_id = page_id
            publish_token = self.access_token

            # Resolve Facebook Page if page_id is 'me' or user token is used
            try:
                acc_res = await client.get(
                    f"https://graph.facebook.com/v19.0/me/accounts?fields=id,name,access_token&access_token={self.access_token}"
                )
                if acc_res.status_code == 200:
                    pages = acc_res.json().get("data", [])
                    if pages:
                        matched_page = next((p for p in pages if str(p.get("id")) == target_page_id), pages[0])
                        target_page_id = str(matched_page.get("id"))
                        publish_token = matched_page.get("access_token") or self.access_token
                        logger.info(f"Targeting Facebook Page '{matched_page.get('name')}' (ID: {target_page_id})")
                    elif target_page_id == "me" or (not pages and target_page_id.isdigit()):
                        me_chk = await client.get(f"https://graph.facebook.com/v19.0/me?fields=id,name&access_token={self.access_token}")
                        if me_chk.status_code == 200 and (target_page_id == "me" or str(me_chk.json().get("id")) == target_page_id):
                            raise ValueError(
                                "Facebook Graph API requires publishing to a Facebook Page. "
                                "Your connected Facebook account does not manage any Facebook Pages, and Meta has deprecated posting to personal profiles (error: (#200) publish_actions deprecated). "
                                "Please create a Facebook Page for your brand at https://facebook.com/pages/create and reconnect your account."
                            )
            except ValueError:
                raise
            except Exception as e:
                logger.debug(f"Page account resolution note: {e}")

            # Use the Page media endpoints so the selected asset is actually
            # uploaded as a photo or video. /feed?link= only creates a link
            # share and silently drops the intended media type.
            if video_url or media_type in {"video", "mediatype.video"}:
                endpoint = f"https://graph.facebook.com/v19.0/{target_page_id}/videos"
                payload = {"description": content_text or "", "file_url": video_url, "access_token": publish_token}
            elif image_url:
                endpoint = f"https://graph.facebook.com/v19.0/{target_page_id}/photos"
                payload = {"caption": content_text or "", "url": image_url, "access_token": publish_token}
            else:
                endpoint = f"https://graph.facebook.com/v19.0/{target_page_id}/feed"
                payload = {"message": content_text or "", "access_token": publish_token}

            res = await client.post(endpoint, data=payload)
            if res.status_code != 200:
                err_text = res.text
                err_code = None
                try:
                    err_json = res.json()
                    err_info = err_json.get("error", {})
                    err_text = err_info.get("message", res.text)
                    err_code = err_info.get("code")
                except Exception:
                    pass
                if "publish_actions" in err_text or "deprecated" in err_text.lower():
                    raise RuntimeError(
                        "Facebook Graph API publish error (HTTP 403): Publishing to personal Facebook timelines is deprecated by Meta ((#200) publish_actions deprecated). "
                        "Meta requires publishing to a Facebook Page. Please create a Facebook Page at https://facebook.com/pages/create and connect it."
                    )
                if "No permission to publish the video" in err_text or err_code in (100, 200) or "pages_manage_posts" in err_text:
                    err_text += " (Requires Facebook Page permission 'pages_manage_posts' and 'pages_read_engagement' as Page Admin.)"
                raise RuntimeError(f"Facebook Graph API publish error (HTTP {res.status_code}): {err_text}")

            post_id = res.json().get("id")
            if not post_id:
                raise RuntimeError(f"Facebook publish response missing post ID: {res.text}")

            logger.info(f"Facebook post published successfully! Live ID: {post_id}")
            return {
                "success": True,
                "platform": "facebook",
                "platform_post_id": str(post_id),
                "permalink": f"https://facebook.com/{post_id}",
                "status": "published",
                "raw_response": res.json()
            }

    async def fetch_analytics(self, account_id: str, since: str = None) -> Dict[str, Any]:
        if not self.access_token:
            return {"impressions": 0, "reach": 0, "engagements": 0, "likes": 0, "comments": 0, "shares": 0, "clicks": 0, "engagement_rate": 0.0}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.get(
                    f"https://graph.facebook.com/v19.0/{account_id}/insights",
                    params={"metric": "page_impressions,page_engaged_users", "access_token": self.access_token}
                )
                if res.status_code == 200:
                    payload = res.json()
                    values = {}
                    for item in payload.get("data", []):
                        name = item.get("name")
                        raw_values = item.get("values") or []
                        if raw_values:
                            value = raw_values[-1].get("value", 0)
                            if isinstance(value, (int, float)):
                                values[name] = int(value)

                    impressions = values.get("page_impressions", 0)
                    engagements = values.get("page_engaged_users", 0)
                    likes = values.get("page_fan_adds", 0)
                    comments = values.get("page_comments", 0)
                    shares = values.get("page_post_engagements", 0)

                    # Page insights do not consistently expose post-level like/comment
                    # counters. Read the recent published posts as a fallback so the
                    # dashboard reflects real engagement instead of silently storing 0.
                    posts_res = await client.get(
                        f"https://graph.facebook.com/v19.0/{account_id}/posts",
                        params={
                            "fields": "likes.limit(0).summary(true),comments.limit(0).summary(true),shares",
                            "limit": "100",
                            "access_token": self.access_token
                        }
                    )
                    if posts_res.status_code == 200:
                        for post in posts_res.json().get("data", []):
                            likes += int((((post.get("likes") or {}).get("summary") or {}).get("total_count", 0)))
                            comments += int((((post.get("comments") or {}).get("summary") or {}).get("total_count", 0)))
                            shares += int(((post.get("shares") or {}).get("count", 0)))

                    engagements = max(engagements, likes + comments + shares)
                    clicks = values.get("page_consumptions", 0)
                    if not clicks:
                        clicks_res = await client.get(
                            f"https://graph.facebook.com/v19.0/{account_id}/insights",
                            params={
                                "metric": "page_consumptions",
                                "access_token": self.access_token
                            }
                        )
                        if clicks_res.status_code == 200:
                            click_data = clicks_res.json().get("data", [])
                            if click_data and click_data[0].get("values"):
                                clicks = int(click_data[0]["values"][-1].get("value", 0))
                    return {
                        "impressions": impressions,
                        "reach": values.get("page_impressions_unique", impressions),
                        "engagements": engagements,
                        "likes": likes,
                        "comments": comments,
                        "shares": shares,
                        "clicks": clicks,
                        "engagement_rate": round((engagements / impressions) * 100.0, 2) if impressions else 0.0,
                        "raw_insights": payload,
                        "status": "connected"
                    }
        except Exception as e:
            logger.error(f"Facebook insights error: {str(e)}")

        return {
            "impressions": 0,
            "reach": 0,
            "engagements": 0,
            "likes": 0,
            "comments": 0,
            "shares": 0,
            "clicks": 0,
            "engagement_rate": 0.0
        }

    async def verify_credentials(self) -> bool:
        if not self.access_token:
            return False
        try:
            details = await self.verify_and_fetch_details()
            return details.get("valid", False)
        except Exception:
            return False

    async def verify_and_fetch_details(self, page_id: str = None) -> Dict[str, Any]:
        """
        Verify Facebook Page credentials against official Meta Graph API.
        """
        if not self.access_token:
            raise ValueError("Facebook verification requires an active Page Access Token.")

        async with httpx.AsyncClient(timeout=15.0) as client:
            # 1. First check /me/accounts to find managed Pages and Page Access Tokens
            try:
                acc_res = await client.get(
                    f"https://graph.facebook.com/v19.0/me/accounts?fields=id,name,link,access_token&access_token={self.access_token}"
                )
                if acc_res.status_code == 200:
                    pages = acc_res.json().get("data", [])
                    if pages:
                        matched = next((p for p in pages if str(p.get("id")) == str(page_id)), pages[0]) if page_id else pages[0]
                        p_id = str(matched.get("id"))
                        p_name = matched.get("name", f"Facebook Page {p_id}")
                        p_link = matched.get("link") or f"https://facebook.com/{p_id}"
                        p_token = matched.get("access_token") or self.access_token
                        return {
                            "valid": True,
                            "platform": "facebook",
                            "account_id": p_id,
                            "account_name": p_name,
                            "display_name": p_name,
                            "profile_url": p_link,
                            "page_access_token": p_token,
                            "permissions": ["pages_show_list", "pages_read_engagement", "pages_manage_posts"]
                        }
            except Exception as e:
                logger.debug(f"me/accounts verification check note: {e}")

            # 2. If explicit page_id provided, test target_id directly
            target_id = page_id or "me"
            res = await client.get(
                f"https://graph.facebook.com/v19.0/{target_id}?fields=id,name,link&access_token={self.access_token}"
            )
            if res.status_code != 200:
                err_data = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}
                err_msg = err_data.get("error", {}).get("message", res.text)
                raise ValueError(f"Facebook Graph API verification failed: {err_msg}")

            page_data = res.json()
            real_page_id = str(page_data.get("id"))
            page_name = page_data.get("name", f"Facebook Page {real_page_id}")
            profile_url = page_data.get("link") or f"https://facebook.com/{real_page_id}"

            # Check if this ID is a personal user profile rather than a page
            if target_id == "me" or real_page_id == target_id:
                raise ValueError(
                    "Connected Meta account is a personal user profile with no Facebook Pages. "
                    "Meta Graph API requires a Facebook Page to publish posts and read insights (personal timeline publishing is deprecated). "
                    "Please create a Facebook Page for your brand at https://facebook.com/pages/create and connect it."
                )

            # Check granted permissions on token
            granted_perms = []
            try:
                perm_res = await client.get(
                    f"https://graph.facebook.com/v19.0/me/permissions?access_token={self.access_token}"
                )
                if perm_res.status_code == 200:
                    perm_data = perm_res.json().get("data", [])
                    granted_perms = [p.get("permission") for p in perm_data if p.get("status") == "granted"]
            except Exception:
                pass

            if granted_perms and "pages_manage_posts" not in granted_perms:
                raise ValueError(
                    "Facebook token is missing the required 'pages_manage_posts' permission. "
                    "Without 'pages_manage_posts', Meta will not permit publishing posts, photos, or videos to this Page. "
                    "Please regenerate or grant a Page Access Token with 'pages_manage_posts' and 'pages_read_engagement'."
                )

            return {
                "valid": True,
                "platform": "facebook",
                "account_id": real_page_id,
                "account_name": page_name,
                "display_name": page_name,
                "profile_url": profile_url,
                "permissions": granted_perms or ["pages_show_list", "pages_read_engagement", "pages_manage_posts"]
            }


    async def refresh_access_token(self, client_id: str = None, client_secret: str = None) -> Dict[str, Any]:
        """
        Refreshes or validates a Facebook access token via Meta Graph API.
        If client credentials are provided, attempts fb_exchange_token extension.
        Otherwise verifies token validity via debug_token or /me.
        """
        token = self.access_token
        if not token:
            raise ValueError("No Facebook access token available to refresh.")

        token = token.strip()

        async with httpx.AsyncClient(timeout=15.0) as client:
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
                except Exception as e:
                    logger.warning(f"Facebook fb_exchange_token attempt failed: {e}")

            # Check debug_token
            try:
                debug_res = await client.get(
                    "https://graph.facebook.com/debug_token",
                    params={"input_token": token, "access_token": token}
                )
                if debug_res.status_code == 200:
                    ddata = debug_res.json().get("data", {})
              
                    if ddata.get("is_valid"):
                        expires_at = ddata.get("expires_at", 0)
                        now_ts = int(datetime.utcnow().timestamp())
                        expires_in = max(0, expires_at - now_ts) if expires_at > 0 else 5184000
                        return {"access_token": token, "expires_in": expires_in}
            except Exception as e:
                logger.warning(f"Facebook debug_token check warning: {e}")

            me_res = await client.get(f"https://graph.facebook.com/v19.0/me?access_token={token}")
            if me_res.status_code == 200:
                return {"access_token": token, "expires_in": 5184000}
            raise RuntimeError(f"Facebook token refresh failed: {me_res.text}")

