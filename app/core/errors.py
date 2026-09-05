from typing import Any, Dict, Optional
from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class DealFlowException(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: Optional[Dict[str, Any]] = None,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(self.message)


class NotFoundError(DealFlowException):
    def __init__(self, message: str = "Resource not found.", code: str = "NOT_FOUND", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            code=code,
            message=message,
            status_code=status.HTTP_404_NOT_FOUND,
            details=details,
        )


class EntityNotFoundError(NotFoundError):
    def __init__(self, entity_name: str, entity_id: Any):
        super().__init__(
            code="ENTITY_NOT_FOUND",
            message=f"{entity_name} with ID '{entity_id}' not found.",
            details={"entity_name": entity_name, "entity_id": str(entity_id)},
        )


class ConflictError(DealFlowException):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            code=code,
            message=message,
            status_code=status.HTTP_409_CONFLICT,
            details=details,
        )


class VersionConflictError(ConflictError):
    def __init__(self, current_version: int, attempted_version: int):
        super().__init__(
            code="STALE_VERSION",
            message=f"Quotation has been modified by another transaction. Expected version {attempted_version}, found {current_version}.",
            details={"current_version": current_version, "attempted_version": attempted_version},
        )


class BusinessRuleError(DealFlowException):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            code=code,
            message=message,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            details=details,
        )


class ValidationError(DealFlowException):
    def __init__(self, message: str = "Validation error.", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            code="VALIDATION_ERROR",
            message=message,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            details=details,
        )


class UnauthorizedError(DealFlowException):
    def __init__(self, message: str = "Authentication required.", code: str = "UNAUTHENTICATED"):
        super().__init__(
            code=code,
            message=message,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )


class ForbiddenError(DealFlowException):
    def __init__(self, message: str = "Permission denied.", code: str = "FORBIDDEN"):
        super().__init__(
            code=code,
            message=message,
            status_code=status.HTTP_403_FORBIDDEN,
        )


async def dealflow_exception_handler(request: Request, exc: DealFlowException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details: Dict[str, Any] = {"errors": []}
    for err in exc.errors():
        field = " -> ".join([str(loc) for loc in err.get("loc", [])])
        details["errors"].append({
            "field": field,
            "message": err.get("msg"),
            "type": err.get("type"),
        })
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request validation failed.",
                "details": details,
            }
        },
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = "HTTP_ERROR"
    if exc.status_code == status.HTTP_401_UNAUTHORIZED:
        code = "UNAUTHENTICATED"
    elif exc.status_code == status.HTTP_403_FORBIDDEN:
        code = "FORBIDDEN"
    elif exc.status_code == status.HTTP_404_NOT_FOUND:
        code = "NOT_FOUND"
    elif exc.status_code == status.HTTP_409_CONFLICT:
        code = "CONFLICT"
    elif exc.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY:
        code = "UNPROCESSABLE_ENTITY"

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": code,
                "message": str(exc.detail),
                "details": {},
            }
        },
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected error occurred.",
                "details": {"exception": str(exc)},
            }
        },
    )
