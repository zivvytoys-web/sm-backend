from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from src.utils.logger import logger
from src.utils.response import error_response


def _attach_cors_headers(response: JSONResponse, request: Request) -> JSONResponse:
    origin = request.headers.get("origin")
    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, PATCH"
        response.headers["Access-Control-Allow-Headers"] = "*"
    return response


class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """Global HTTP middleware that intercepts unhandled server exceptions."""

    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin")
        try:
            return await call_next(request)
        except HTTPException as http_exc:
            resp = error_response(
                detail=str(http_exc.detail),
                status_code=http_exc.status_code,
                error_code="HTTP_EXCEPTION"
            )
            if http_exc.headers:
                for k, v in http_exc.headers.items():
                    resp.headers[k] = v
            return _attach_cors_headers(resp, request)
        except Exception as exc:
            logger.exception(f"Unhandled server error on {request.method} {request.url.path}: {str(exc)}")
            from app.core.config import settings
            if getattr(settings, "DEBUG", True) is False or getattr(settings, "ENVIRONMENT", "").lower() in ["production", "prod"]:
                err_detail = "An internal server error occurred. Please contact support."
            else:
                err_detail = str(exc).strip() or "An internal server error occurred."
            resp = error_response(
                detail=err_detail,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="INTERNAL_SERVER_ERROR"
            )
            return _attach_cors_headers(resp, request)


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Formats Pydantic request validation errors into a clean standardized JSON format."""
    origin = request.headers.get("origin")
    errors = exc.errors()
    msg = "; ".join([f"{'.'.join(str(l) for l in err.get('loc', []))}: {err.get('msg')}" for err in errors])
    resp = error_response(
        detail=msg,
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        error_code="VALIDATION_ERROR"
    )
    return _attach_cors_headers(resp, request)


__all__ = ["ErrorHandlingMiddleware", "validation_exception_handler"]

