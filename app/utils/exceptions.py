from fastapi import HTTPException, status


class AppException(HTTPException):
    def __init__(self, status_code: int, detail: str, error_code: str = "ERROR"):
        super().__init__(status_code=status_code, detail=detail)
        self.error_code = error_code


class EntityNotFoundException(AppException):
    def __init__(self, entity_name: str, entity_id: str | int):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{entity_name} with identifier '{entity_id}' was not found.",
            error_code="ENTITY_NOT_FOUND"
        )


class PermissionDeniedException(AppException):
    def __init__(self, detail: str = "You do not have permission to perform this action."):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
            error_code="PERMISSION_DENIED"
        )


class QuotaExceededException(AppException):
    def __init__(self, detail: str = "Monthly AI generation quota has been exceeded."):
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=detail,
            error_code="QUOTA_EXCEEDED"
        )


class SocialIntegrationException(AppException):
    def __init__(self, platform: str, detail: str):
        super().__init__(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Integration error on {platform}: {detail}",
            error_code="SOCIAL_INTEGRATION_ERROR"
        )
