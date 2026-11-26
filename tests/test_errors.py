import httpx
from fastapi import FastAPI, status

from app.errors import AppError


class _TeapotError(AppError):
    status_code = status.HTTP_418_IM_A_TEAPOT
    code = "teapot"


async def test_app_error_is_rendered_as_json(app: FastAPI, client: httpx.AsyncClient) -> None:
    @app.get("/api/_raise")
    async def _raise() -> None:
        raise _TeapotError("short and stout")

    response = await client.get("/api/_raise")

    assert response.status_code == 418
    assert response.json() == {"detail": "short and stout", "code": "teapot"}
