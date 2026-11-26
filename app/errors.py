"""Application error type and its JSON handler."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.request_logging import REQUEST_ID_HEADER


class AppError(Exception):
    """Base error that maps to a JSON response `{"detail": ..., "code": ...}`."""

    status_code: int = 400
    code: str = "app_error"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        request.state.error_code = exc.code
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "code": exc.code},
        )

    @app.exception_handler(500)
    async def handle_unexpected_error(request: Request, _: Exception) -> PlainTextResponse:
        # Starlette generates the fallback response outside the request middleware.
        return PlainTextResponse(
            "Internal Server Error",
            status_code=500,
            headers={REQUEST_ID_HEADER: request.state.request_id},
        )
