from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

from loguru import logger

from .settings import Settings


LOG_FORMAT = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function}:{line} | {message}"


class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame = logging.currentframe()
        depth = 2
        while frame is not None and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def _is_non_error_record(record: dict) -> bool:
    return record["level"].no < logging.ERROR


def configure_logging(
    *,
    debug: bool,
    settings: Settings,
    now: datetime | None = None,
) -> tuple[Path, Path]:
    info_log_path, error_log_path = settings.log_file_paths(now=now)
    info_log_path.parent.mkdir(parents=True, exist_ok=True)
    error_log_path.parent.mkdir(parents=True, exist_ok=True)

    logger.remove()
    if sys.stderr is not None:
        logger.add(
            sys.stderr,
            level="DEBUG" if debug else "INFO",
            format=LOG_FORMAT,
            backtrace=debug,
            diagnose=debug,
        )
    logger.add(
        info_log_path,
        level="DEBUG" if debug else "INFO",
        format=LOG_FORMAT,
        encoding="utf-8",
        filter=_is_non_error_record,
        backtrace=debug,
        diagnose=debug,
    )
    logger.add(
        error_log_path,
        level="ERROR",
        format=LOG_FORMAT,
        encoding="utf-8",
        backtrace=debug,
        diagnose=debug,
    )

    logging.captureWarnings(True)
    root_logger = logging.getLogger()
    root_logger.handlers = [InterceptHandler()]
    root_logger.setLevel(logging.DEBUG if debug else logging.INFO)

    for name in list(root_logger.manager.loggerDict.keys()):
        target_logger = logging.getLogger(name)
        target_logger.handlers = []
        target_logger.propagate = True

    return info_log_path, error_log_path
