from typing import Dict, Any, List
import httpx
from app.integrations.social.base import BaseSocialClient
from app.core.logging import logger


class TwitterClient(BaseSocialClient):
    """X (Twitter) API v2 Integration Adapter."""

    async def publish_post(self, content_text: str, media_urls: List[str] = None, **kwargs) -> Dict[str, Any]:
        logger.info(f"Publishing to X (Twitter): tweet='{content_text[:30]}...'")
        if not self.access_token:
            raise ValueError(
                "X (Twitter) publishing requires an active OAuth 2.0 Access Token with 'tweet.write' scope. "
                "Please connect your X account with valid credentials."
            )

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = {
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json"
                }
                payload = {"text": content_text}
                res = await client.post("https://api.twitter.com/2/tweets", headers=headers, json=payload)
                if res.status_code in (200, 201):
                    data = res.json().get("data", {})
                    tweet_id = data.get("id")
                    return {
                        "success": True,
                        "platform": "twitter",
                        "platform_post_id": tweet_id,
                        "permalink": f"https://x.com/user/status/{tweet_id}",
                        "status": "published",
                        "raw_response": res.json()
                    }
                else:
                    error_detail = res.text
                    if "unsupported-authentication" in error_detail or "OAuth 2.0 Application-Only is forbidden" in error_detail:
                        raise RuntimeError(
                            "X (Twitter) API error (HTTP 403 Unsupported Authentication): "
                            "The stored token is an Application-Only Bearer Token. "
                            "Twitter API requires a User Context Token (OAuth 2.0 User Token with 'tweet.write' scope, "
                            "or OAuth 1.0a User Access Token + Secret with Read & Write permissions) to post tweets. "
                            "Please connect X using OAuth 2.0 User Context."
                        )
                    raise RuntimeError(f"X (Twitter) API error (HTTP {res.status_code}): {error_detail}")
        except Exception as e:
            logger.error(f"X (Twitter) publish failed: {str(e)}")
            raise

    async def fetch_analytics(self, account_id: str, since: str = None) -> Dict[str, Any]:
        if not self.access_token:
            return {"impressions": 0, "engagements": 0, "retweets": 0, "likes": 0}
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                headers = {"Authorization": f"Bearer {self.access_token}"}
                res = await client.get(f"https://api.twitter.com/2/users/{account_id}/tweets?tweet.fields=public_metrics", headers=headers)
                if res.status_code == 200:
                    return {"raw_analytics": res.json(), "status": "connected"}
        except Exception as e:
            logger.error(f"X analytics fetch error: {str(e)}")

        return {"impressions": 0, "engagements": 0, "retweets": 0, "likes": 0}

    async def refresh_access_token(self, refresh_token: str, client_id: str, client_secret: str = "") -> Dict[str, Any]:
        """Refreshes X (Twitter) OAuth 2.0 token using Twitter API v2 token endpoint."""
        if not refresh_token:
            raise ValueError("Refresh token is required to refresh X (Twitter) credentials.")

        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id
        }
        auth = (client_id, client_secret) if client_secret else None

        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post("https://api.twitter.com/2/oauth2/token", data=data, auth=auth)
            if res.status_code == 200:
                return res.json()
            raise RuntimeError(f"X (Twitter) token refresh failed (HTTP {res.status_code}): {res.text}")

    async def verify_and_fetch_details(self) -> Dict[str, Any]:
        """
        Verify X (Twitter) credentials via Twitter API v2 /users/me.
        """
        if not self.access_token:
            raise ValueError("X (Twitter) verification requires an active OAuth 2.0 access token or Bearer token.")

        headers = {"Authorization": f"Bearer {self.access_token}"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.get(
                "https://api.twitter.com/2/users/me?user.fields=id,name,username,profile_image_url,verified",
                headers=headers
            )
            if res.status_code != 200:
                err_text = res.text
                raise ValueError(f"X (Twitter) API verification failed (HTTP {res.status_code}): {err_text}")

            data = res.json().get("data", {})
            user_id = data.get("id")
            username = data.get("username")
            name = data.get("name", username)
            return {
                "valid": True,
                "platform": "twitter",
                "account_id": str(user_id),
                "account_name": f"@{username}" if username else name,
                "display_name": name,
                "profile_url": f"https://x.com/{username}" if username else "https://x.com",
                "permissions": ["tweet.read", "tweet.write", "users.read", "offline.access"]
            }

    async def verify_credentials(self) -> bool:
        """Verify X (Twitter) token validity."""
        try:
            details = await self.verify_and_fetch_details()
            return bool(details.get("valid"))
        except Exception:
            return False

