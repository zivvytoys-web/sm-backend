"""
Security Headers Middleware.

Implements OWASP recommended security headers:
- Content-Security-Policy (CSP)
- X-Frame-Options (Clickjacking defense)
- X-Content-Type-Options (MIME sniffing defense)
- X-XSS-Protection
- Strict-Transport-Security (HSTS)
- Referrer-Policy
- Permissions-Policy
"""
from typing import Callable
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.core.config import settings


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds enterprise security headers to every outgoing HTTP response."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        # 1. Clickjacking defense
        response.headers["X-Frame-Options"] = "DENY"

        # 2. MIME sniffing defense
        response.headers["X-Content-Type-Options"] = "nosniff"

        # 3. Cross-Site Scripting (XSS) filter
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # 4. Referrer Policy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # 5. Permissions Policy (restricts browser capabilities)
        response.headers["Permissions-Policy"] = (
            "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
            "magnetometer=(), microphone=(), payment=(), usb=()"
        )

        # 6. Content Security Policy (allows necessary CDNs: Cloudinary, Google fonts, inline scripts for Vite)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self' 'unsafe-inline' https: data: blob: res.cloudinary.com; "
            "img-src 'self' data: https: res.cloudinary.com blob:; "
            "media-src 'self' https: res.cloudinary.com blob:; "
            "connect-src 'self' https: wss:; "
            "font-src 'self' https: data:; "
            "frame-ancestors 'none';"
        )

        # 7. Strict-Transport-Security (HSTS) for HTTPS in production
        if getattr(settings, "ENVIRONMENT", "").lower() in ("production", "prod") or request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"

        return response
