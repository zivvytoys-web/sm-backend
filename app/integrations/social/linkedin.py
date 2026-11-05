from typing import Dict, Any, List
import httpx
from app.integrations.social.base import BaseSocialClient
from app.core.logging import logger


class LinkedInClient(BaseSocialClient):
    """LinkedIn Community Management API v2 client."""

    async def publish_post(self, content_text: str, media_urls: List[str] = None, **kwargs) -> Dict[str, Any]:
        logger.info(f"Publishing to LinkedIn: text='{content_text[:30]}...'")
        if not self.access_token:
            raise ValueError(
                "LinkedIn publishing requires an active OAuth 2.0 Access Token with 'w_member_social' or 'w_organization_social' scope. "
                "Please connect your LinkedIn account or provide an API access token."
            )

        try:
            author = kwargs.get("author", "urn:li:person:me")
            async with httpx.AsyncClient(timeout=30.0) as client:
                endpoint = "https://api.linkedin.com/v2/ugcPosts"
                headers = {
                    "Authorization": f"Bearer {self.access_token}",
                    "X-Restli-Protocol-Version": "2.0.0",
                    "Content-Type": "application/json"
                }
                payload = {
                    "author": author,
                    "lifecycleState": "PUBLISHED",
                    "specificContent": {
                        "com.linkedin.ugc.ShareContent": {
                            "shareCommentary": {"text": content_text},
                            "shareMediaCategory": "NONE"
                        }
                    },
                    "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"}
                }
                res = await client.post(endpoint, headers=headers, json=payload)
                if res.status_code not in (200, 201):
                    raise RuntimeError(f"LinkedIn API publish error (HTTP {res.status_code}): {res.text}")

                post_id = res.json().get("id")
                return {
                    "success": True,
                    "platform": "linkedin",
                    "platform_post_id": post_id,
                    "permalink": f"https://linkedin.com/feed/update/{post_id}",
                    "status": "published",
                    "raw_response": res.json()
                }
        except Exception as e:
            logger.error(f"LinkedIn API error: {str(e)}")
            raise

    async def fetch_analytics(self, account_id: str, since: str = None) -> Dict[str, Any]:
        return {"impressions": 0, "reach": 0, "engagements": 0, "likes": 0, "comments": 0, "shares": 0, "clicks": 0, "engagement_rate": 0.0}

    async def verify_credentials(self) -> bool:
        if not self.access_token:
            return False
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                headers = {"Authorization": f"Bearer {self.access_token}"}
                res = await client.get("https://api.linkedin.com/v2/userinfo", headers=headers)
                return res.status_code == 200
        except Exception:
            return False
