from typing import Any, Optional

from fastapi import Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class APIError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: Optional[list[dict[str, Any]]] = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details

    def to_response(self) -> JSONResponse:
        body: dict[str, Any] = {"error": {"code": self.code, "message": self.message}}
        if self.details:
            body["error"]["details"] = self.details
        return JSONResponse(status_code=self.status_code, content=body)


class NotFoundError(APIError):
    def __init__(self, message: str):
        super().__init__(status.HTTP_404_NOT_FOUND, "NOT_FOUND", message)


class ValidationError(APIError):
    def __init__(self, message: str, details: Optional[list[dict[str, Any]]] = None):
        super().__init__(status.HTTP_400_BAD_REQUEST, "VALIDATION_ERROR", message, details)


class UnauthorizedError(APIError):
    def __init__(self, message: str, code: str = "UNAUTHORIZED"):
        super().__init__(status.HTTP_401_UNAUTHORIZED, code, message)


class ForbiddenError(APIError):
    def __init__(self, message: str):
        super().__init__(status.HTTP_403_FORBIDDEN, "FORBIDDEN", message)


class ConflictError(APIError):
    def __init__(self, message: str):
        super().__init__(status.HTTP_409_CONFLICT, "CONFLICT", message)


async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    return exc.to_response()


async def request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {"field": ".".join(str(p) for p in err["loc"] if p not in ("body", "query", "path")), "issue": err["msg"]}
        for err in exc.errors()
    ]
    body = {"error": {"code": "VALIDATION_ERROR", "message": "Request validation failed", "details": details}}
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content=jsonable_encoder(body))
