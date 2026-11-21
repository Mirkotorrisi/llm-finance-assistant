"""Keep bank authorization codes out of the access log.

Uvicorn logs every request line, and the bank's redirect carries the single-use ``code`` in the query string of
``/banking/callback``. The code is useless without the private key, but a secret in a log file is still a secret in
a log file: this filter blanks it (and ``state``) before the line is written.
"""

from __future__ import annotations

import logging
import re

_SENSITIVE = re.compile(r"((?:code|state)=)[^&\s\"]+")


def redact_query(text: str) -> str:
    return _SENSITIVE.sub(r"\1***", text)


class RedactCallbackFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.args and isinstance(record.args, tuple):
            # httpx logs URL objects, uvicorn plain strings: compare on the text either way
            record.args = tuple(redact_query(str(a)) if "/banking/callback" in str(a) else a for a in record.args)
        if isinstance(record.msg, str) and "/banking/callback" in record.msg:
            record.msg = redact_query(record.msg)
        return True


def install() -> None:
    for name in ("uvicorn.access", "uvicorn.error", "httpx"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactCallbackFilter) for f in logger.filters):
            logger.addFilter(RedactCallbackFilter())
