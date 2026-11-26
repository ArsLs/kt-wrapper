"""Start the API with the same logging configuration in development and Docker."""

import argparse
import logging
from logging.config import dictConfig

import uvicorn
from pydantic import ValidationError

from app.config import get_settings
from app.logging_config import log_event, logging_config

logger = logging.getLogger("app.startup")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Keitaro Wrapper API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    try:
        settings = get_settings()
    except ValidationError as exc:
        dictConfig(logging_config("INFO"))
        log_event(logger, logging.ERROR, "startup.invalid_settings", exc_info=exc)
        raise SystemExit(1) from None

    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_config=logging_config(settings.log_level),
        access_log=False,
    )


if __name__ == "__main__":
    main()
