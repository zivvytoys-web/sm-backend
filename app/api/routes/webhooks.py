import hmac
import hashlib
import base64
import json
from typing import Optional, Dict, Any
from fastapi import APIRouter, Request, Response, Query, status
from fastapi.responses import PlainTextResponse, JSONResponse

from app.core.config import settings
from app.core.logging import logger

router = APIRouter(prefix="/webhooks", tags=["Universal Social Media Webhooks"])


# =========================================================================
# 1. META (FACEBOOK & INSTAGRAM) WEBHOOKS
# =========================================================================

@router.get("/meta", response_class=PlainTextResponse)
@router.get("/facebook", response_class=PlainTextResponse)
@router.get("/instagram", response_class=PlainTextResponse)
def meta_webhook_verification(
    request: Request,
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge")
):
    """
    Meta (Facebook & Instagram) Webhook Handshake Verification.
    Meta's servers send a GET request with hub.mode=subscribe, hub.verify_token, and hub.challenge.
    Returns hub.challenge as plain text upon token verification.
    """
    expected_token = getattr(settings, "META_WEBHOOK_VERIFY_TOKEN", "social_ai_meta_verify_token_2026_994757e6")

    logger.info(f"[MetaWebhook] Verification attempt: mode={hub_mode}, token={hub_verify_token}")

    if hub_mode == "subscribe" and hub_verify_token == expected_token:
        logger.info(f"[MetaWebhook] Verification successful! Returning challenge: {hub_challenge}")
        return PlainTextResponse(content=str(hub_challenge), status_code=200)

    logger.warning(f"[MetaWebhook] Verification failed! Expected: '{expected_token}', Received: '{hub_verify_token}'")
    return Response(content="Forbidden: Invalid verify token", status_code=403)


@router.post("/meta")
@router.post("/facebook")
@router.post("/instagram")
async def meta_webhook_events(request: Request):
    """
    Meta Real-Time Event Ingestion (Page posts, Instagram mentions, feed comments, publish updates).
    """
    try:
        body = await request.body()
        payload = json.loads(body.decode("utf-8")) if body else {}
    except Exception as e:
        logger.warning(f"[MetaWebhook] Failed to parse payload: {e}")
        payload = {}

    logger.info(f"[MetaWebhook] Event received: object={payload.get('object')}")
    return {"status": "EVENT_RECEIVED", "object": payload.get("object")}


# =========================================================================
# 2. GOOGLE / YOUTUBE PUBSUB WEBHOOKS
# =========================================================================

@router.get("/youtube", response_class=PlainTextResponse)
def youtube_webhook_verification(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
    hub_topic: Optional[str] = Query(None, alias="hub.topic")
):
    """
    YouTube PubSubHubbub Verification Handshake.
    """
    if hub_challenge:
        return PlainTextResponse(content=str(hub_challenge), status_code=200)
    return PlainTextResponse(content="OK", status_code=200)


@router.post("/youtube")
async def youtube_webhook_events(request: Request):
    """
    YouTube video upload and channel update event feed.
    """
    try:
        body = await request.body()
    except Exception:
        body = b""
    return {"status": "YOUTUBE_EVENT_RECEIVED", "bytes": len(body)}


# =========================================================================
# 3. X (TWITTER) WEBHOOKS & CRC (CHALLENGE-RESPONSE CHECK)
# =========================================================================

@router.get("/twitter")
def twitter_crc_verification(crc_token: Optional[str] = Query(None)):
    """
    Twitter / X Account Activity API CRC Check.
    Requires HMAC-SHA256 hash of crc_token using the consumer secret.
    """
    if not crc_token:
        return JSONResponse(status_code=400, content={"error": "crc_token query parameter missing"})

    consumer_secret = getattr(settings, "TWITTER_API_SECRET", "") or getattr(settings, "TWITTER_CLIENT_SECRET", "")
    if not consumer_secret:
        consumer_secret = "default_twitter_crc_secret"

    sha256_hash_digest = hmac.new(
        key=consumer_secret.encode("utf-8"),
        msg=crc_token.encode("utf-8"),
        digestmod=hashlib.sha256
    ).digest()

    response_token = f"sha256={base64.b64encode(sha256_hash_digest).decode('utf-8')}"
    return {"response_token": response_token}


@router.post("/twitter")
async def twitter_webhook_events(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return {"status": "TWITTER_EVENT_RECEIVED"}


# =========================================================================
# 4. TIKTOK WEBHOOKS
# =========================================================================

@router.get("/tiktok", response_class=PlainTextResponse)
def tiktok_webhook_verification(challenge: Optional[str] = Query(None)):
    """
    TikTok Webhook Verification Challenge.
    """
    if challenge:
        return PlainTextResponse(content=str(challenge), status_code=200)
    return PlainTextResponse(content="OK", status_code=200)


@router.post("/tiktok")
async def tiktok_webhook_events(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return {"status": "TIKTOK_EVENT_RECEIVED"}


# =========================================================================
# 5. LINKEDIN WEBHOOKS
# =========================================================================

@router.get("/linkedin", response_class=PlainTextResponse)
def linkedin_webhook_verification(challengeCode: Optional[str] = Query(None, alias="challengeCode")):
    """
    LinkedIn Webhook Challenge Code Verification.
    """
    if challengeCode:
        return PlainTextResponse(content=str(challengeCode), status_code=200)
    return PlainTextResponse(content="OK", status_code=200)


@router.post("/linkedin")
async def linkedin_webhook_events(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return {"status": "LINKEDIN_EVENT_RECEIVED"}


# =========================================================================
# 6. PINTEREST & SNAPCHAT WEBHOOKS
# =========================================================================

@router.get("/pinterest", response_class=PlainTextResponse)
def pinterest_webhook_verification(challenge: Optional[str] = Query(None)):
    if challenge:
        return PlainTextResponse(content=str(challenge), status_code=200)
    return PlainTextResponse(content="OK", status_code=200)


@router.post("/pinterest")
async def pinterest_webhook_events(request: Request):
    return {"status": "PINTEREST_EVENT_RECEIVED"}


@router.get("/snapchat", response_class=PlainTextResponse)
def snapchat_webhook_verification(challenge: Optional[str] = Query(None)):
    if challenge:
        return PlainTextResponse(content=str(challenge), status_code=200)
    return PlainTextResponse(content="OK", status_code=200)


@router.post("/snapchat")
async def snapchat_webhook_events(request: Request):
    return {"status": "SNAPCHAT_EVENT_RECEIVED"}
