from datetime import date, datetime
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field


class AnalyticsRecord(BaseModel):
    id: int
    social_account_id: int
    date: date
    impressions: int = 0
    reach: int = 0
    views: int = 0
    engagements: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    clicks: int = 0
    engagement_rate: float = 0.0
    raw_metrics: Dict[str, Any] = {}
    created_at: Optional[datetime] = None
    platform: Optional[str] = None
    account_name: Optional[str] = None

    class Config:
        from_attributes = True


class OverviewAnalyticsSummary(BaseModel):
    total_impressions: int
    total_reach: int
    total_engagements: int
    total_posts: int
    average_engagement_rate: float
    history: List[AnalyticsRecord] = []


class PlatformBreakdownItem(BaseModel):
    platform: str
    post_count: int
    percentage: float
    impressions: int = 0
    reach: int = 0
    views: int = 0
    engagements: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    engagement_rate: float = 0.0


class FormatBreakdownItem(BaseModel):
    format: str
    count: int
    percentage: float


class TimeSeriesItem(BaseModel):
    date: str
    posts_scheduled: int = 0
    posts_published: int = 0
    impressions: int = 0
    reach: int = 0
    views: int = 0
    engagements: int = 0


class TopPostItem(BaseModel):
    id: int
    title: Optional[str] = None
    caption: Optional[str] = None
    platform: str
    status: str
    scheduled_time: Optional[str] = None
    published_time: Optional[str] = None
    media_url: Optional[str] = None
    platform_post_id: Optional[str] = None
    platform_url: Optional[str] = None
    views: int = 0
    impressions: int = 0
    reach: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    clicks: int = 0
    engagement_rate: float = 0.0
    metrics_last_synced_at: Optional[str] = None


class ConnectedAccountItem(BaseModel):
    id: int
    platform: str
    account_name: str
    is_connected: bool
    last_synced_at: Optional[str] = None
    followers_count: Optional[int] = None
    error_message: Optional[str] = None
    permissions_status: Optional[str] = "valid"


class DashboardAnalyticsResponse(BaseModel):
    period: str
    start_date: str
    end_date: str
    total_posts_created: int
    posts_scheduled: int
    posts_published: int
    posts_failed: int
    publication_success_rate: float
    success_rate_formula: str
    total_impressions: int
    total_reach: int
    views: int = 0
    total_engagements: int
    likes: int
    comments: int
    shares: int
    clicks: int
    engagement_rate: float
    engagement_rate_formula: str
    connected_accounts_count: int
    is_live_data: bool
    status_message: str
    last_synced_at: Optional[str] = None
    platform_breakdown: List[PlatformBreakdownItem] = Field(default_factory=list)
    format_breakdown: List[FormatBreakdownItem] = Field(default_factory=list)
    time_series: List[TimeSeriesItem] = Field(default_factory=list)
    top_posts: List[TopPostItem] = Field(default_factory=list)
    accounts: List[ConnectedAccountItem] = Field(default_factory=list)
    history: List[Dict[str, Any]] = Field(default_factory=list)
    platform_metrics: Dict[str, Any] = Field(default_factory=dict)
    sync_status: Dict[str, Any] = Field(default_factory=dict)

