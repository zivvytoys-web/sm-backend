from abc import ABC, abstractmethod
from typing import Dict, Any, List


class BaseSocialClient(ABC):
    """Abstract Base Class for all social media provider adapters."""

    def __init__(self, access_token: str | None = None, refresh_token: str | None = None):
        self.access_token = access_token
        self.refresh_token = refresh_token

    @abstractmethod
    async def publish_post(self, content_text: str, media_urls: List[str] = None, **kwargs) -> Dict[str, Any]:
        """Publish post to the platform and return response metadata with external post ID."""
        pass

    @abstractmethod
    async def fetch_analytics(self, account_id: str, since: str = None) -> Dict[str, Any]:
        """Fetch engagement and impression analytics from platform APIs."""
        pass

    @abstractmethod
    async def verify_credentials(self) -> bool:
        """Verify token validity."""
        pass
