import logging
import re
import traceback
import uuid
from pathlib import Path


_UNSAFE_CONTEXT_CHARS = re.compile(r"[^A-Za-z0-9_.:@/-]+")


def _safe_context_value(value: object) -> str:
    text = str(value or "")
    return _UNSAFE_CONTEXT_CHARS.sub("_", text).strip("_")[:120] or "unknown"


def _context_suffix(context: dict[str, object]) -> str:
    if not context:
        return ""
    fields = " ".join(
        f"{_safe_context_value(key)}={_safe_context_value(value)}"
        for key, value in sorted(context.items())
    )
    return f" {fields}" if fields else ""


def _stack_summary(exc: BaseException) -> str:
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return "unavailable"
    return ">".join(
        f"{_safe_context_value(Path(frame.filename).name)}:"
        f"{frame.lineno}:{_safe_context_value(frame.name)}"
        for frame in frames[-12:]
    )


def log_safe_exception(
    logger: logging.Logger,
    event: str,
    exc: BaseException,
    *,
    level: int = logging.ERROR,
    **context: object,
) -> None:
    """Log useful failure location/type data without the exception message."""
    logger.log(
        level,
        "%s diagnostic_id=%s exception_type=%s stack=%s%s",
        _safe_context_value(event),
        uuid.uuid4().hex[:12],
        _safe_context_value(type(exc).__name__),
        _stack_summary(exc),
        _context_suffix(context),
    )


def log_safe_external_failure(
    logger: logging.Logger,
    event: str,
    detail: object,
    *,
    level: int = logging.WARNING,
    **context: object,
) -> None:
    """Record an external failure without retaining its untrusted raw detail."""
    try:
        detail_length = len(str(detail or ""))
    except Exception:
        detail_length = -1
    logger.log(
        level,
        "%s diagnostic_id=%s detail_type=%s detail_length=%s%s",
        _safe_context_value(event),
        uuid.uuid4().hex[:12],
        _safe_context_value(type(detail).__name__),
        detail_length,
        _context_suffix(context),
    )
