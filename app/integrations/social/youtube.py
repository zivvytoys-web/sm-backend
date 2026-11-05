from typing import Dict, Any, List, Optional
from datetime import datetime
from pathlib import Path
import mimetypes
import httpx

from app.integrations.social.base import BaseSocialClient
from app.core.logging import logger
from app.core.config import settings
from app.core.security import sanitize_credential_token
from app.services.youtube_token_manager import (
    YouTubeError,
    YouTubeAuthError,
    YouTubeQuotaError,
    YouTubeAPIError
)


class YouTubeClient(BaseSocialClient):
    """
    YouTube Data API v3 video/Shorts integration client.
    Supports Google OAuth 2.0 Access Tokens (ya29...) and token refresh.
    Enables publishing for:
    - YouTube Shorts & Videos
    """

    def __init__(
        self,
        access_token: str | None = None,
        refresh_token: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None
    ):
        clean_access = sanitize_credential_token(access_token, "YouTube access token", allow_empty=True)
        clean_refresh = sanitize_credential_token(refresh_token, "YouTube refresh token", allow_empty=True)
        super().__init__(access_token=clean_access, refresh_token=clean_refresh)
        self.client_id = sanitize_credential_token(client_id, "YouTube client ID", allow_empty=True) or getattr(settings, "YOUTUBE_CLIENT_ID", None)
        self.client_secret = sanitize_credential_token(client_secret, "YouTube client secret", allow_empty=True) or getattr(settings, "YOUTUBE_CLIENT_SECRET", None)

    async def refresh_access_token(
        self,
        refresh_token: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Refresh expired Google OAuth 2.0 access token using refresh_token.
        Updates self.access_token in place and returns dictionary with tokens and expires_in.
        Preserves existing refresh_token if Google does not return a new one.
        """
        raw_to_use = refresh_token or self.refresh_token
        if not raw_to_use:
            logger.warning("YouTubeClient: No refresh_token available to refresh access_token.")
            raise YouTubeAuthError("No refresh token available to refresh YouTube access token.")

        token_to_use = sanitize_credential_token(raw_to_use, "YouTube refresh token", allow_empty=False)
        cid = sanitize_credential_token(client_id or self.client_id or getattr(settings, "YOUTUBE_CLIENT_ID", None), "YouTube client ID", allow_empty=False)
        csec = sanitize_credential_token(client_secret or self.client_secret or getattr(settings, "YOUTUBE_CLIENT_SECRET", None), "YouTube client secret", allow_empty=False)

        if not cid or not csec:
            logger.warning("YouTubeClient: Client ID or Client Secret missing. Cannot refresh token.")
            raise YouTubeAuthError("YouTube OAuth client credentials missing in configuration.")

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "client_id": cid,
                        "client_secret": csec,
                        "refresh_token": token_to_use,
                        "grant_type": "refresh_token"
                    }
                )

                if res.status_code == 200:
                    data = res.json()
                    new_token = data.get("access_token")
                    if new_token:
                        self.access_token = new_token
                        new_refresh = data.get("refresh_token")
                        if new_refresh:
                            self.refresh_token = new_refresh
                        logger.info("YouTubeClient: Successfully refreshed Google OAuth access token.")
                        return {
                            "access_token": new_token,
                            "refresh_token": self.refresh_token,
                            "expires_in": data.get("expires_in", 3600),
                            "scope": data.get("scope", "")
                        }
                    raise YouTubeAuthError("Google refresh response missing 'access_token'.")

                err_json = {}
                try:
                    err_json = res.json()
                except Exception:
                    pass
                err_type = err_json.get("error", "")
                err_desc = err_json.get("error_description", res.text[:200])

                if err_type in ("invalid_grant", "unauthorized_client") or "revoked" in err_desc.lower() or "expired" in err_desc.lower():
                    raise YouTubeAuthError(
                        f"YouTube OAuth authorization expired or revoked ({err_type}: {err_desc}). Please reconnect."
                    )
                raise YouTubeAPIError(f"YouTube token refresh failed (HTTP {res.status_code}): {err_desc}")

        except (YouTubeAuthError, YouTubeAPIError):
            raise
        except Exception as exc:
            logger.error(f"YouTubeClient: Exception during token refresh: {exc}")
            raise YouTubeAPIError(f"Network error refreshing YouTube token: {exc}")

    async def publish_post(self, content_text: str, media_urls: List[str] = None, **kwargs) -> Dict[str, Any]:
        logger.info(f"Publishing to YouTube: title='{(content_text or '')[:30]}...'")

        if not self.access_token and self.refresh_token:
            logger.info("No active access token for YouTube. Auto-refreshing via refresh_token...")
            try:
                await self.refresh_access_token()
            except Exception as e:
                logger.warning(f"Initial token refresh failed: {e}")

        if not self.access_token:
            raise YouTubeAuthError(
                "YouTube publishing requires an active Google OAuth Access Token with 'youtube.upload' scope. "
                "Please connect your YouTube channel with valid credentials."
            )

        if self.access_token.startswith("AIza"):
            raise YouTubeAuthError(
                "The stored YouTube token is a Google Cloud API Key ('AIza...'). "
                "YouTube Data API requires an OAuth 2.0 User Access Token ('ya29...') with 'youtube.upload' scope. "
                "API keys cannot upload videos or manage channels. Please authenticate via Google OAuth."
            )

        video_urls = [
            u for u in (media_urls or [])
            if any(ext in u.lower() for ext in ('.mp4', '.mov', '.avi', '.mkv', '.webm', 'video'))
        ]
        if not video_urls:
            raise ValueError(
                "YouTube is a video platform and only accepts video formats (.mp4, .mov, .webm). "
                "The selected post contains an image. To publish to YouTube, please attach a video or select video generation."
            )

        post_format = kwargs.get("post_format", "video")
        if post_format == "community":
            raise ValueError(
                "YouTube Data API v3 supports real video/Short uploads, but does not provide a public API for "
                "Community Tab publishing. Attach an MP4, MOV, or WebM video to publish to YouTube."
            )

        try:
            video_url = video_urls[0]
        except Exception as e:
            raise RuntimeError(f"Unable to select a video asset for YouTube upload: {e}") from e

        async with httpx.AsyncClient(timeout=120.0) as client:
            # 1. Read a local upload or download a remotely hosted video.
            video_bytes = None
            storage_root = Path(settings.LOCAL_STORAGE_DIR).resolve()

            # Strategy A: Direct file on disk (absolute or relative)
            if os.path.isfile(video_url):
                logger.info(f"Reading direct local video file '{video_url}' for YouTube upload")
                video_bytes = Path(video_url).read_bytes()

            # Strategy B: Stored upload path (e.g. /uploads/media/ai_videos/...)
            elif video_url.startswith(("/uploads/", "uploads/")):
                rel_path = video_url.lstrip("/\\")
                if rel_path.startswith("uploads/"):
                    rel_path = rel_path[len("uploads/"):]
                elif rel_path.startswith("uploads\\"):
                    rel_path = rel_path[len("uploads\\"):]

                cand_path = (storage_root / rel_path).resolve()
                if cand_path.is_file():
                    logger.info(f"Reading local video {cand_path.name} from storage_root for YouTube upload")
                    video_bytes = cand_path.read_bytes()
                else:
                    # Check candidate subdirectories
                    bname = os.path.basename(video_url)
                    for cand_dir in [
                        storage_root / "media" / "ai_videos",
                        storage_root / "media" / "processed_videos",
                        Path(".media_tmp").resolve(),
                    ]:
                        cand = (cand_dir / bname).resolve()
                        if cand.is_file():
                            logger.info(f"Reading local video {cand.name} from {cand_dir} for YouTube upload")
                            video_bytes = cand.read_bytes()
                            break

            # Strategy C: Remote HTTP/HTTPS URL
            elif video_url.startswith(("http://", "https://")):
                logger.info(f"Downloading video from {video_url[:60]}... for YouTube upload")
                video_res = await client.get(video_url)
                if video_res.status_code != 200:
                    raise RuntimeError(f"Failed to fetch video file from {video_url} (HTTP {video_res.status_code})")
                video_bytes = video_res.content

            # Strategy D: Fallback to searching filename across common storage paths
            else:
                bname = os.path.basename(video_url)
                found = False
                for cand_dir in [
                    storage_root / "media" / "ai_videos",
                    storage_root / "media" / "processed_videos",
                    storage_root,
                    Path(".media_tmp").resolve(),
                ]:
                    cand = (cand_dir / bname).resolve()
                    if cand.is_file():
                        logger.info(f"Resolved video {bname} in fallback search {cand_dir}")
                        video_bytes = cand.read_bytes()
                        found = True
                        break
                if not found:
                    raise RuntimeError(
                        f"The selected video path '{video_url}' was not found on local disk or as an HTTPS URL."
                    )

            if not video_bytes:
                raise RuntimeError("The selected video file is empty.")

            mime_type = mimetypes.guess_type(video_url)[0] or "video/mp4"

            # 2. Initiate Resumable Upload
            title = kwargs.get("title") or (content_text or "Social Post")[:100]
            description = content_text or ""

            # Ensure YouTube Shorts are tagged appropriately
            if "#shorts" not in title.lower() and "#shorts" not in description.lower():
                if len(title) <= 90:
                    title = f"{title.strip()} #Shorts"
                else:
                    description = f"{description}\n\n#Shorts"

            init_headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": mime_type,
                "X-Upload-Content-Length": str(len(video_bytes))
            }
            init_body = {
                "snippet": {
                    "title": title[:100],
                    "description": description,
                    "categoryId": "22"
                },
                "status": {
                    "privacyStatus": kwargs.get("privacy_status", "public")
                }
            }
            init_endpoint = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
            init_res = await client.post(init_endpoint, headers=init_headers, json=init_body)

            # If 401 Unauthorized, automatically try refreshing access token with refresh_token and retry once
            if init_res.status_code == 401 and self.refresh_token:
                logger.info("YouTube OAuth access token expired (HTTP 401). Attempting automatic refresh...")
                try:
                    refresh_result = await self.refresh_access_token()
                    if refresh_result.get("access_token"):
                        init_headers["Authorization"] = f"Bearer {self.access_token}"
                        init_res = await client.post(init_endpoint, headers=init_headers, json=init_body)
                except Exception as ref_err:
                    logger.warning(f"YouTubeClient inline refresh attempt failed: {ref_err}")

            if init_res.status_code not in (200, 201):
                err_text = init_res.text
                err_reason = ""
                try:
                    err_json = init_res.json()
                    err_info = err_json.get("error", {})
                    err_text = err_info.get("message", init_res.text)
                    errors_list = err_info.get("errors", [])
                    if errors_list:
                        err_reason = errors_list[0].get("reason", "")
                except Exception:
                    pass

                if init_res.status_code == 401:
                    raise YouTubeAuthError(
                        f"YouTube OAuth token expired or unauthorized (HTTP 401): {err_text}. "
                        "Please reconnect your YouTube channel in Social Accounts."
                    )
                if init_res.status_code == 403:
                    if "quotaExceeded" in err_reason or "quota" in err_text.lower():
                        raise YouTubeQuotaError(f"YouTube Data API quota exceeded: {err_text}")
                    raise YouTubeAuthError(f"YouTube upload forbidden (HTTP 403): {err_text}")

                raise YouTubeAPIError(f"YouTube upload initiation failed (HTTP {init_res.status_code}): {err_text}")

            upload_location = init_res.headers.get("Location")
            if not upload_location:
                raise RuntimeError("YouTube upload initiation did not return upload Location URL.")

            # 3. Stream binary video content to YouTube upload location
            logger.info(f"Uploading {len(video_bytes)} bytes to YouTube...")
            upload_headers = {
                "Content-Type": mime_type,
                "Content-Length": str(len(video_bytes))
            }
            upload_res = await client.put(upload_location, headers=upload_headers, content=video_bytes)

            if upload_res.status_code not in (200, 201):
                err_text = upload_res.text
                try:
                    err_json = upload_res.json()
                    err_text = err_json.get("error", {}).get("message", upload_res.text)
                except Exception:
                    pass
                raise YouTubeAPIError(f"YouTube video upload failed (HTTP {upload_res.status_code}): {err_text}")

            video_data = upload_res.json()
            video_id = video_data.get("id")
            if not video_id:
                raise RuntimeError(f"YouTube response missing video ID: {upload_res.text}")

            logger.info(f"YouTube video uploaded successfully! Live ID: {video_id}")
            return {
                "success": True,
                "platform": "youtube",
                "platform_post_id": str(video_id),
                "permalink": f"https://youtube.com/watch?v={video_id}",
                "status": "published",
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "raw_response": video_data
            }

    async def fetch_analytics(self, account_id: str, since: str = None) -> Dict[str, Any]:
        """
        Fetches live channel and video analytics via YouTube Data API v3.
        Supports auto-refreshing expired OAuth tokens.
        """
        token = self.access_token
        if not token and self.refresh_token:
            try:
                await self.refresh_access_token()
                token = self.access_token
            except Exception as e:
                logger.warning(f"YouTube auto-refresh before analytics fetch failed: {e}")

        if not token:
            return {
                "impressions": 0, "reach": 0, "views": 0, "engagements": 0,
                "likes": 0, "comments": 0, "shares": 0, "clicks": 0,
                "engagement_rate": 0.0, "status": "unauthorized",
                "error": "Missing YouTube access token."
            }

        headers = {"Authorization": f"Bearer {token}"}
        stats = {
            "impressions": 0, "reach": 0, "views": 0, "engagements": 0,
            "likes": 0, "comments": 0, "shares": 0, "clicks": 0,
            "engagement_rate": 0.0, "status": "connected"
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                url = f"{self.API_BASE}/channels"
                params = {"part": "statistics,snippet", "mine": "true"}
                res = await client.get(url, headers=headers, params=params)
                if res.status_code == 401 and self.refresh_token:
                    await self.refresh_access_token()
                    headers["Authorization"] = f"Bearer {self.access_token}"
                    res = await client.get(url, headers=headers, params=params)

                if res.status_code == 200:
                    items = res.json().get("items", [])
                    if items:
                        ch_stats = items[0].get("statistics", {})
                        view_count = int(ch_stats.get("viewCount", 0))
                        comment_count = int(ch_stats.get("commentCount", 0))
                        subscriber_count = int(ch_stats.get("subscriberCount", 0))
                        stats["views"] = view_count
                        stats["impressions"] = view_count
                        stats["reach"] = subscriber_count or view_count
                        stats["comments"] = comment_count
                        stats["raw_channel_stats"] = ch_stats
                elif res.status_code != 200:
                    err_msg = res.json().get("error", {}).get("message", res.text[:200])
                    stats["error"] = f"YouTube Channel API ({res.status_code}): {err_msg}"
            except Exception as e:
                logger.warning(f"YouTube channel stats fetch error: {e}")
                stats["error"] = str(e)

        return stats

    async def fetch_post_analytics(self, video_id: str) -> Dict[str, Any]:
        """
        Fetches live statistics for a specific published YouTube video or Short.
        """
        token = self.access_token
        if not token:
            return {"views": 0, "likes": 0, "comments": 0, "impressions": 0, "reach": 0, "engagement_rate": 0.0}

        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                res = await client.get(
                    f"{self.API_BASE}/videos",
                    headers=headers,
                    params={"part": "statistics,snippet", "id": video_id}
                )
                if res.status_code == 200:
                    items = res.json().get("items", [])
                    if items:
                        st = items[0].get("statistics", {})
                        views = int(st.get("viewCount", 0))
                        likes = int(st.get("likeCount", 0))
                        comments = int(st.get("commentCount", 0))
                        engagements = likes + comments
                        rate = round((engagements / views * 100.0), 2) if views > 0 else 0.0
                        return {
                            "views": views,
                            "impressions": views,
                            "reach": views,
                            "likes": likes,
                            "comments": comments,
                            "shares": 0,
                            "engagements": engagements,
                            "engagement_rate": rate,
                            "status": "connected"
                        }
            except Exception as e:
                logger.warning(f"YouTube video {video_id} stats fetch error: {e}")

        return {"views": 0, "likes": 0, "comments": 0, "impressions": 0, "reach": 0, "engagement_rate": 0.0}

    async def verify_credentials(self) -> bool:
        if not self.access_token:
            return False
        try:
            details = await self.verify_and_fetch_details()
            return details.get("valid", False)
        except Exception:
            return False

    async def verify_and_fetch_details(self, channel_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Verify YouTube credentials via Google Data API v3.
        Supports both Google API Keys (AIza...) and Google OAuth 2.0 Bearer tokens.
        Supports automatic inline token refresh if 401 is received.
        """
        if not self.access_token and self.refresh_token:
            logger.info("No active access token for YouTube. Auto-refreshing via refresh_token...")
            try:
                await self.refresh_access_token()
            except Exception as e:
                logger.warning(f"Verification token refresh failed: {e}")

        if not self.access_token:
            raise YouTubeAuthError("YouTube verification requires an active Google OAuth 2.0 access token.")

        try:
            token = sanitize_credential_token(self.access_token, "YouTube access token", allow_empty=False)
        except ValueError as val_err:
            raise YouTubeAuthError(str(val_err))

        if token.startswith("AIza"):
            raise YouTubeAuthError(
                "You provided a Google Cloud API Key ('AIza...'). "
                "Google API keys cannot authenticate channel ownership or upload videos. "
                "YouTube requires an OAuth 2.0 User Access Token ('ya29...') with 'youtube.upload' scope. "
                "Recommended: Use '⚡ 1-Click Login with YouTube' for automatic authorization and auto-refresh."
            )

        if token.startswith("GOCSPX-"):
            raise YouTubeAuthError(
                "You provided a Google OAuth Client Secret ('GOCSPX-...'), not an Access Token. "
                "A Client Secret is an application-level credential used for server-side token exchange, not a user token. "
                "Please click the '⚡ 1-Click Login with YouTube' button to connect your channel securely via official OAuth 2.0."
            )

        headers = {"Authorization": f"Bearer {token}"}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                ch_res = await client.get(
                    "https://www.googleapis.com/youtube/v3/channels?part=snippet,contentDetails&mine=true",
                    headers=headers
                )
                # If 401 and refresh_token available, refresh and retry once
                if ch_res.status_code == 401 and self.refresh_token:
                    logger.info("YouTube verification returned 401. Refreshing access token...")
                    try:
                        ref_data = await self.refresh_access_token()
                        if ref_data.get("access_token"):
                            token = sanitize_credential_token(self.access_token, "YouTube access token", allow_empty=False)
                            headers = {"Authorization": f"Bearer {token}"}
                            ch_res = await client.get(
                                "https://www.googleapis.com/youtube/v3/channels?part=snippet,contentDetails&mine=true",
                                headers=headers
                            )
                    except Exception as ref_err:
                        logger.warning(f"YouTubeClient inline verification refresh failed: {ref_err}")

                if ch_res.status_code == 200:
                    data = ch_res.json()
                    items = data.get("items", [])
                    if items:
                        channel = items[0]
                        ch_id = channel.get("id")
                        snippet = channel.get("snippet", {})
                        title = snippet.get("title", f"YouTube Channel {ch_id}")
                        custom_url = snippet.get("customUrl")
                        return {
                            "valid": True,
                            "platform": "youtube",
                            "account_id": str(ch_id),
                            "account_name": custom_url or title,
                            "display_name": title,
                            "profile_url": f"https://youtube.com/{custom_url or f'channel/{ch_id}'}",
                            "permissions": ["youtube.upload", "youtube.readonly", "userinfo.profile"],
                            "access_token": token,
                            "refresh_token": self.refresh_token
                        }

                # Fallback to Google OAuth userinfo
                user_res = await client.get("https://www.googleapis.com/oauth2/v3/userinfo", headers=headers)
                if user_res.status_code == 200:
                    user_data = user_res.json()
                    sub_id = user_data.get("sub")
                    name = user_data.get("name", "YouTube Creator")
                    return {
                        "valid": True,
                        "platform": "youtube",
                        "account_id": str(sub_id),
                        "account_name": name,
                        "display_name": name,
                        "profile_url": "https://youtube.com",
                        "permissions": ["youtube.upload", "youtube.readonly", "userinfo.profile"],
                        "access_token": token,
                        "refresh_token": self.refresh_token
                    }

                if ch_res.status_code == 401:
                    if not self.refresh_token:
                        raise YouTubeAuthError(
                            "Your Google OAuth Access Token has expired or is invalid (HTTP 401). "
                            "Google access tokens ('ya29...') expire automatically after 1 hour (3600 seconds). "
                            "To resolve this: (1) Re-generate a fresh Access Token in Google OAuth Playground (https://developers.google.com/oauthplayground/) "
                            "with the 'https://www.googleapis.com/auth/youtube.upload' scope. "
                            "(2) Provide both your Access Token ('ya29...') and Refresh Token ('1//04...') so the platform can automatically renew it for you."
                        )
                    else:
                        raise YouTubeAuthError(
                            "Google rejected the YouTube access token (HTTP 401 Unauthorized), and automatic token refresh could not be completed. "
                            "Please ensure your Google OAuth Client ID and Secret are configured in Settings > Integrations, "
                            "or generate a fresh Access Token in Google OAuth Playground."
                        )
                if ch_res.status_code == 403:
                    err_text = ch_res.text
                    if "quotaExceeded" in err_text:
                        raise YouTubeQuotaError("YouTube API daily quota limit reached (10,000 units/day). Requests will resume after Google resets quota at midnight Pacific Time.")
                    elif "accessNotConfigured" in err_text or "has not been used" in err_text:
                        raise YouTubeAuthError("YouTube Data API v3 is not enabled in your Google Cloud project. Please visit Google Cloud Console > APIs & Services > Library and click 'Enable' on YouTube Data API v3.")
                    else:
                        raise YouTubeAuthError(f"YouTube API access forbidden (HTTP 403): {ch_res.text[:200]}")

                raise YouTubeAPIError(f"YouTube credentials verification failed (HTTP {ch_res.status_code}): {ch_res.text[:200]}")
        except UnicodeEncodeError:
            raise YouTubeAuthError("YouTube credentials contain invalid non-ASCII characters that cannot be encoded for authorization.")
        except httpx.RequestError as req_err:
            raise YouTubeAPIError("Network communication error with YouTube API.")
