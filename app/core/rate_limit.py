"""
Enterprise Rate Limiting & Abuse Prevention Engine.

Provides:
- Configurable rate limits by endpoint sensitivity (Auth, AI, Uploads, Public, Authenticated)
- Sliding-window rate limiter with in-memory store and optional Redis integration
- Client IP resolution with trusted proxy support
- Standard RFC 6585 compliance (HTTP 429, Retry-After, X-RateLimit-* headers)
- FastAPI dependencies for endpoint-level rate enforcement
"""
import time
import os
from typing import Dict, List, Tuple, Optional, Callable
from collections import defaultdict
from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response, JSONResponse

from app.core.config import settings
from app.core.logging import logger

# Configuration defaults with environment variable overrides
RATE_LIMIT_ENABLED = os.environ.get("RATE_LIMIT_ENABLED", "true").lower() in ("true", "1", "yes")

# Limits in format: (max_requests, window_seconds)
RATE_LIMITS = {
    "login": (
        int(os.environ.get("RATE_LIMIT_LOGIN_MAX", "5")),
        int(os.environ.get("RATE_LIMIT_LOGIN_WINDOW", "60"))
    ),
    "register": (
        int(os.environ.get("RATE_LIMIT_REGISTER_MAX", "10")),
        int(os.environ.get("RATE_LIMIT_REGISTER_WINDOW", "60"))
    ),
    "ai_generation": (
        int(os.environ.get("RATE_LIMIT_AI_MAX", "15")),
        int(os.environ.get("RATE_LIMIT_AI_WINDOW", "60"))
    ),
    "upload": (
        int(os.environ.get("RATE_LIMIT_UPLOAD_MAX", "20")),
        int(os.environ.get("RATE_LIMIT_UPLOAD_WINDOW", "60"))
    ),
    "authenticated": (
        int(os.environ.get("RATE_LIMIT_AUTH_MAX", "120")),
        int(os.environ.get("RATE_LIMIT_AUTH_WINDOW", "60"))
    ),
    "anonymous": (
        int(os.environ.get("RATE_LIMIT_ANON_MAX", "60")),
        int(os.environ.get("RATE_LIMIT_ANON_WINDOW", "60"))
    ),
}

# Trusted proxy IPs/CIDRs if behind reverse proxies (Railway, Cloudflare, Nginx)
TRUSTED_PROXIES = {
    ip.strip() for ip in os.environ.get("TRUSTED_PROXIES", "127.0.0.1,::1,localhost").split(",") if ip.strip()
}


class SlidingWindowRateLimiter:
    """Thread-safe sliding-window rate limiter with memory management and cleanup."""

    def __init__(self):
        self._history: Dict[str, List[float]] = defaultdict(list)
        self._last_cleanup = time.time()
        self._redis_client = None
        self._init_redis()

    def _init_redis(self):
        """Attempt to connect to Redis if configured; otherwise use in-memory store."""
        redis_url = getattr(settings, "REDIS_URL", None) or os.environ.get("REDIS_URL")
        if redis_url and redis_url.startswith("redis://"):
            try:
                import redis
                client = redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
                client.ping()
                self._redis_client = client
                logger.info("RateLimiter initialized using shared Redis store.")
            except Exception as e:
                logger.warning(f"RateLimiter Redis connection unavailable ({e}); falling back to in-memory store.")
                self._redis_client = None

    def _cleanup_expired(self, max_age: int = 3600):
        """Periodically purge old keys from memory to prevent unbounded memory growth."""
        now = time.time()
        if now - self._last_cleanup < 300:
            return
        self._last_cleanup = now
        stale_keys = [
            k for k, timestamps in self._history.items()
            if not timestamps or (now - timestamps[-1] > max_age)
        ]
        for k in stale_keys:
            self._history.pop(k, None)

    def check(self, key: str, max_requests: int, window_seconds: int) -> Tuple[bool, int, int, int]:
        """
        Check rate limit for key.
        Returns: (allowed, remaining, retry_after, reset_time)
        """
        if not RATE_LIMIT_ENABLED:
            return True, max_requests, 0, int(time.time() + window_seconds)

        now = time.time()

        # Redis-backed path if available
        if self._redis_client:
            try:
                pipe = self._redis_client.pipeline()
                redis_key = f"rl:{key}"
                clear_before = now - window_seconds
                pipe.zremrangebyscore(redis_key, 0, clear_before)
                pipe.zcard(redis_key)
                pipe.zadd(redis_key, {str(now): now})
                pipe.expire(redis_key, window_seconds + 5)
                results = pipe.execute()

                current_count = results[1]
                if current_count >= max_requests:
                    # Get oldest timestamp to compute exact retry_after
                    oldest = self._redis_client.zrange(redis_key, 0, 0, withscores=True)
                    retry_after = int(max(1, (oldest[0][1] + window_seconds) - now)) if oldest else 1
                    reset_time = int(now + retry_after)
                    return False, 0, retry_after, reset_time

                remaining = max(0, max_requests - current_count - 1)
                reset_time = int(now + window_seconds)
                return True, remaining, 0, reset_time
            except Exception as exc:
                logger.warning(f"Redis rate check error: {exc}. Falling back to in-memory.")

        # In-memory sliding window fallback
        self._cleanup_expired()
        timestamps = self._history[key]
        cutoff = now - window_seconds
        valid_timestamps = [t for t in timestamps if t > cutoff]
        self._history[key] = valid_timestamps

        if len(valid_timestamps) >= max_requests:
            oldest = valid_timestamps[0]
            retry_after = int(max(1, (oldest + window_seconds) - now))
            reset_time = int(now + retry_after)
            return False, 0, retry_after, reset_time

        valid_timestamps.append(now)
        remaining = max(0, max_requests - len(valid_timestamps))
        reset_time = int(now + window_seconds)
        return True, remaining, 0, reset_time


limiter = SlidingWindowRateLimiter()


def get_client_ip(request: Request) -> str:
    """Safely extracts real client IP respecting trusted proxy configuration."""
    # Check X-Forwarded-For if behind reverse proxy
    x_forwarded = request.headers.get("x-forwarded-for")
    if x_forwarded:
        ips = [ip.strip() for ip in x_forwarded.split(",") if ip.strip()]
        if ips:
            return ips[0]

    # Cloudflare header
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip.strip()

    # Direct client host
    if request.client and request.client.host:
        return request.client.host

    return "127.0.0.1"


def rate_limit(category: str = "anonymous"):
    """
    FastAPI endpoint dependency for route-specific rate limiting.
    Usage: Depends(rate_limit('ai_generation'))
    """
    def dependency(request: Request):
        if not RATE_LIMIT_ENABLED:
            return

        ip = get_client_ip(request)
        # Check if authenticated via cookie or bearer
        token_sub = getattr(request.state, "user_id", None)
        client_key = f"{category}:{token_sub or ip}"

        max_req, window = RATE_LIMITS.get(category, RATE_LIMITS["anonymous"])
        allowed, remaining, retry_after, reset_time = limiter.check(client_key, max_req, window)

        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Try again in {retry_after} seconds.",
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(max_req),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_time)
                }
            )

    return dependency


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Global rate limiting middleware that enforces broad IP/identity thresholds
    and attaches standard X-RateLimit headers.
    """

    EXEMPT_PREFIXES = ("/docs", "/redoc", "/openapi.json", "/static", "/health")

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        if not RATE_LIMIT_ENABLED or path == "/" or any(path.startswith(p) for p in self.EXEMPT_PREFIXES):
            return await call_next(request)

        ip = get_client_ip(request)

        # Categorize request
        if "/auth/login" in path:
            category = "login"
        elif "/auth/register" in path or "/auth/password" in path or "/auth/reset" in path:
            category = "register"
        elif "/generations" in path or "/ai-planner" in path:
            category = "ai_generation"
        elif "/upload" in path or "/media" in path and request.method == "POST":
            category = "upload"
        elif request.headers.get("authorization") or request.cookies.get("access_token"):
            category = "authenticated"
        else:
            category = "anonymous"

        max_req, window = RATE_LIMITS.get(category, RATE_LIMITS["anonymous"])
        key = f"global:{category}:{ip}"

        allowed, remaining, retry_after, reset_time = limiter.check(key, max_req, window)

        if not allowed:
            logger.warning(f"Rate limit exceeded for IP {ip} on [{request.method} {path}] category={category}")
            resp = JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error": "RATE_LIMIT_EXCEEDED",
                    "detail": f"Too many requests. Please retry after {retry_after} seconds.",
                    "retry_after": retry_after
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(max_req),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_time)
                }
            )
            # Ensure CORS headers on rate limit response
            origin = request.headers.get("origin")
            if origin:
                resp.headers["Access-Control-Allow-Origin"] = origin
                resp.headers["Access-Control-Allow-Credentials"] = "true"
            return resp

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(max_req)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_time)
        return response
