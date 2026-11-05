from typing import Any, Optional, Dict
from fastapi.responses import JSONResponse
from fastapi import status


def success_response(
    data: Any = None,
    message: str = "Operation successful",
    status_code: int = status.HTTP_200_OK,
    meta: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Generates a standardized JSON response dictionary."""
    res = {
        "success": True,
        "message": message,
        "data": data
    }
    if meta is not None:
        res["meta"] = meta
    return res


def error_response(
    detail: str = "An error occurred",
    status_code: int = status.HTTP_400_BAD_REQUEST,
    error_code: Optional[str] = None
) -> JSONResponse:
    """Generates a standardized JSON error response."""
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "error": error_code or "ERROR",
            "detail": detail
        }
    )


__all__ = ["success_response", "error_response"]
