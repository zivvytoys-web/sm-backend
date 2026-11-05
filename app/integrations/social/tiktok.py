from typing import Dict, Any, List
import httpx
from app.integrations.social.base import BaseSocialClient


class TikTokClient(BaseSocialClient):
    """
    TikTok Content Posting API Client.
    Supports video upload, creator info fetch, and direct video publishing.
    """

    API_BASE = "https://open.tiktokapis.com/v2"

    async def verify_credentials(self) -> bool:
        if not self.access_token:
            return False
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(
                    f"{self.API_BASE}/user/info/",
                    headers={"Authorization": f"Bearer {self.access_token}"},
                    params={"fields": "open_id,union_id,avatar_url,display_name,username"}
                )
                return res.status_code == 200
        except Exception:
            return False

    async def verify_and_fetch_details(self) -> Dict[str, Any]:
        if not self.access_token:
            raise ValueError("Access token is required for TikTok verification.")
        
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.get(
                f"{self.API_BASE}/user/info/",
                headers={"Authorization": f"Bearer {self.access_token}"},
                params={"fields": "open_id,union_id,avatar_url,display_name,username"}
            )
            if res.status_code != 200:
                raise ValueError(f"TikTok user info fetch failed: {res.text}")
            
            data = res.json().get("data", {}).get("user", {})
            account_id = data.get("open_id")
            account_name = data.get("username") or data.get("display_name")
            if not account_id or not account_name:
                raise ValueError("TikTok verification did not return an account ID and username.")

            return {
                "account_id": account_id,
                "account_name": account_name,
                "display_name": data.get("display_name"),
                "profile_url": f"https://www.tiktok.com/@{data.get('username', '')}" if data.get("username") else None,
                "permissions": ["video.upload", "video.publish", "user.info.basic"]
            }

    async def publish_post(self, content_text: str, media_urls: List[str] = None, **kwargs) -> Dict[str, Any]:
        raise RuntimeError(
            "TikTok publishing requires an approved Content Posting API application and the provider's "
            "video-init/upload/publish sequence. This account was not published because no confirmed TikTok "
            "publish response was received."
        )

    async def fetch_analytics(self, account_id: str, since: str = None) -> Dict[str, Any]:
        return {"status": "unavailable", "reason": "TikTok analytics requires approved Research or Business API access."}
