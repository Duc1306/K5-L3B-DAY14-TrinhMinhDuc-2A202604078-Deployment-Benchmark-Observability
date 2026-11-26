"""
Structured logging (tiêu chí 10): mỗi dòng log là MỘT JSON object hợp lệ.

Dựng cả dict rồi json.dumps một lần — không nhét JSON vào trong format string
(cách đó sinh ra JSON lồng không escape, json.loads sẽ lỗi).
"""
import json
import logging
import sys
from datetime import datetime, timezone

from app.config import settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "lvl": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if fields:
            payload.update(fields)
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _build_logger() -> logging.Logger:
    log = logging.getLogger("agent")
    if not log.handlers:  # tránh gắn handler 2 lần khi module được import lại
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        log.addHandler(handler)
    log.setLevel(settings.log_level)
    log.propagate = False  # không đi qua root logger (format mặc định, không phải JSON)
    return log


logger = _build_logger()


def log_event(event: str, level: int = logging.INFO, exc_info=None, **fields) -> None:
    logger.log(level, event, extra={"fields": fields}, exc_info=exc_info)
