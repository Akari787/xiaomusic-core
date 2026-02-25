import re

REDACTED = "***REDACTED***"


_SENSITIVE_KEYS = (
    "token",
    "refresh_token",
    "authorization",
    "cookie",
    "api_key",
    "password",
)


def redact_text(text: str) -> str:
    if not text:
        return text

    # Bearer <token>
    text = re.sub(r"(?i)(bearer\s+)([^\s,;]+)", rf"\1{REDACTED}", text)

    # key=value or key: value
    for key in _SENSITIVE_KEYS:
        pattern = re.compile(
            rf"(?i)({re.escape(key)}\s*[:=]\s*)([^\s,;\"']+|\"[^\"]*\"|'[^']*')"
        )
        text = pattern.sub(rf"\1{REDACTED}", text)
    return text


class RedactingFormatter:
    """A logging formatter that redacts sensitive data in the final message."""

    def __init__(self, base_formatter):
        self._base = base_formatter

    def format(self, record):
        msg = self._base.format(record)
        return redact_text(msg)
