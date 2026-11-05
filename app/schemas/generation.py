from datetime import datetime
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from app.utils.enums import GenerationType, JobStatus, PlatformType


class GenerateTextRequest(BaseModel):
    brand_profile_id: Optional[int] = None
    prompt: str
    target_platforms: List[PlatformType] = [PlatformType.INSTAGRAM]
    tone_override: Optional[str] = None
    target_audience: Optional[str] = None
    include_hashtags: bool = True
    model_provider: str = "gemini"  # gemini, openai, anthropic


class GenerateImageRequest(BaseModel):
    brand_profile_id: Optional[int] = None
    prompt: str
    aspect_ratio: str = "1:1"  # 1:1, 16:9, 9:16, 4:3, 3:4
    style: Optional[str] = "modern minimal, professional studio lighting"
    provider: str = "imagen-3"  # imagen-3, stability, openai


class GeminiTextGenerationRequest(BaseModel):
    prompt: str = Field(..., min_length=2, description="Topic, theme, or core message for generation")
    brand_profile_id: Optional[int] = None
    target_platforms: List[PlatformType] = Field(default=[PlatformType.INSTAGRAM])
    content_type: str = Field(default="caption", description="caption | description | hashtags | marketing_campaign | variations | video_script")
    tone: str = Field(default="Engaging", description="Professional | Engaging | Humorous | Inspirational | Bold | Luxury | Educational | Urgency")
    language: str = Field(default="English", description="Target language e.g. English, Spanish, French, German, Hindi, etc.")
    additional_instructions: Optional[str] = None
    model_name: str = Field(default="gemini-3.1-flash-lite", description="Gemini model identifier")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    num_variations: int = Field(default=3, ge=1, le=5)
    variation_seed: Optional[str] = Field(default=None, max_length=100)


class GeminiImageGenerationRequest(BaseModel):
    prompt: str = Field(..., min_length=3, description="Detailed visual prompt describing scene, subject, lighting, and composition")
    brand_profile_id: Optional[int] = None
    aspect_ratio: str = Field(default="1:1", description="1:1 | 9:16 | 16:9 | 4:3 | 3:4")
    style_preset: Optional[str] = Field(default="modern_product", description="photo | minimalist | 3d_render | cinematic | neon | luxury | artistic")
    negative_prompt: Optional[str] = Field(default="blurry, distorted, low quality, artifacts, watermark", description="Elements to exclude")
    model_name: str = Field(default="gemini-2.5-flash-image", description="Image model version")
    number_of_images: int = Field(default=1, ge=1, le=4)


class GeminiVideoGenerationRequest(BaseModel):
    prompt: str = Field(..., min_length=3, description="Detailed video action prompt describing movement, camera angle, and scene transition")
    brand_profile_id: Optional[int] = None
    aspect_ratio: str = Field(default="16:9", description="16:9 (Landscape) | 9:16 (Vertical Reel/Short)")
    duration_seconds: int = Field(default=30, ge=5, le=60, description="Video duration in seconds (5 to 60, default 30s commercial reel)")
    fps: int = Field(default=30, description="Frames per second (typically 30)")
    resolution: str = Field(default="720p", description="720p | 1080p")
    model_name: str = Field(default="veo-3.1-fast-generate-preview", description="Google Veo video model")
    image_base64: Optional[str] = Field(default=None, description="Optional starting frame image encoded in base64")
    image_url: Optional[str] = Field(default=None, description="Optional starting frame image public URL")
    voice: Optional[str] = Field(default=None, description="AI voice persona")
    provider: Optional[str] = Field(default="auto", description="auto | huggingface | cinema-director | ltx-video")
    max_budget_inr: Optional[float] = Field(default=100.0, description="Maximum budget in INR before requiring confirmation")
    test_mode: Optional[bool] = Field(default=False, description="Prioritize free tier compute and credits")


class DailyTrendingGoalResponse(BaseModel):
    brand_name: str
    campaign_goal: str
    analytics_rationale: str
    target_platform: str = "instagram"
    suggested_format: str = "Video Reel / Short"
    trending_hashtags: List[str] = []
    creative_angle: str = "Viral Velocity & Trend Hook"
    metrics_summary: Dict[str, Any] = {}


class AutoPromptRequest(BaseModel):
    brand_profile_id: Optional[int] = None
    campaign_goal: Optional[str] = Field(default=None, description="Optional campaign goal. If omitted, empty, or 'trending', AI sets daily trending goal via analytics.")
    custom_focus: Optional[str] = None
    target_platform: Optional[PlatformType] = PlatformType.INSTAGRAM
    freshness_seed: Optional[str] = None
    creative_angle: Optional[str] = None


class AutoPromptResponse(BaseModel):
    brand_name: str
    campaign_goal: str
    creative_angle: Optional[str] = None
    text_prompt: str
    image_prompt: str
    video_prompt: str
    suggested_headline: str
    suggested_tone: str
    suggested_aspect_ratio: str
    recommended_hashtags: List[str] = []
    analytics_rationale: Optional[str] = None


class FullBundleGenerationRequest(BaseModel):
    brand_profile_id: Optional[int] = None
    prompt: Optional[str] = None
    campaign_goal: str = Field(default="product_launch")
    target_platforms: List[PlatformType] = Field(default=[PlatformType.INSTAGRAM, PlatformType.YOUTUBE])
    aspect_ratio: str = Field(default="1:1")
    tone: Optional[str] = None
    language: str = Field(default="English")
    voice: Optional[str] = Field(default=None, description="AI voice persona")
    provider: Optional[str] = Field(default="auto", description="AI video engine provider (auto, ltx-video, huggingface, multi-scene-cinema-director)")
    resolution: Optional[str] = Field(default="1080p", description="Video resolution (1080p, 4k)")
    variation_seed: Optional[str] = Field(default=None, max_length=100)


class FullBundleResponse(BaseModel):
    text: Dict[str, Any]
    image: Dict[str, Any]
    video: Dict[str, Any]
    brand_name: str
    created_at: str


class GeminiStatusResponse(BaseModel):
    api_key_configured: bool
    text_generation_available: bool
    image_generation_available: bool
    video_generation_available: bool
    supported_models: Dict[str, List[str]]
    active_key_preview: Optional[str] = None
    default_models: Optional[Dict[str, str]] = None
    discovered_models_count: Optional[int] = None
    api_authenticated: Optional[bool] = None
    message: str


class GenerationJobResponse(BaseModel):
    id: int
    user_id: int
    job_type: GenerationType
    status: JobStatus
    prompt: str
    model_name: Optional[str] = None
    parameters: Dict[str, Any] = {}
    result_data: Dict[str, Any] = {}
    error_message: Optional[str] = None
    tokens_used: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
