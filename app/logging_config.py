"""JSON logging shared by the application and Uvicorn."""

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from traceback import walk_tb

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
        }
        if (current_request_id := request_id.get()) is not None:
            entry["request_id"] = current_request_id
        if hasattr(record, "event_fields"):
            entry.update(record.event_fields)
        elif isinstance(record.msg, BaseException):
            entry["message"] = type(record.msg).__name__
        else:
            entry["message"] = record.getMessage()
        if record.exc_info and (error_type := record.exc_info[0]) is not None:
            entry["error_type"] = error_type.__name__
            # Driver and validation exceptions can include input values in their messages.
            entry["stack"] = [
                f"{frame.f_globals.get('__name__', '?')}.{frame.f_code.co_name}:{line}"
                for frame, line in walk_tb(record.exc_info[2])
            ]
        return json.dumps(entry)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    *,
    exc_info: BaseException | None = None,
    **fields: str | int | float | bool | None,
) -> None:
    logger.log(level, event, extra={"event_fields": {"event": event, **fields}}, exc_info=exc_info)


def logging_config(level: str) -> dict[str, object]:
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"json": {"()": JsonFormatter}},
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": level,
                "formatter": "json",
                "stream": "ext://sys.stdout",
            },
        },
        "root": {"handlers": ["console"], "level": level},
        "loggers": {
            "uvicorn": {"handlers": [], "level": "NOTSET", "propagate": True},
            "uvicorn.error": {"handlers": [], "level": "NOTSET", "propagate": True},
            "uvicorn.access": {"handlers": [], "propagate": False},
            "httpx": {"level": "WARNING"},
            "httpcore": {"level": "WARNING"},
        },
    }
