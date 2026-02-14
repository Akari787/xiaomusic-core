import logging

from xiaomusic.security.redaction import redact_text


class RedactingLogFormatter(logging.Formatter):
    """Formatter that redacts sensitive key/value patterns."""

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        return redact_text(msg)
