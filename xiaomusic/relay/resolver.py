"""Resolve public page URL into direct media source URL."""

from __future__ import annotations

import json

from xiaomusic.relay.contracts import ResolveResult
from xiaomusic.relay.ytdlp_parser import parse_ytdlp_output
from xiaomusic.relay.ytdlp_runner import YtdlpRunner


class Resolver:
    def __init__(self, runner: YtdlpRunner | None = None) -> None:
        self.runner = runner or YtdlpRunner()

    def resolve(self, url: str, timeout_seconds: float = 8) -> ResolveResult:
        run = self.runner.run(url=url, timeout_seconds=timeout_seconds)

        if run.timed_out:
            return ResolveResult(
                ok=False,
                source_url="",
                title="",
                is_live=False,
                container_hint="unknown",
                error_code="E_RESOLVE_TIMEOUT",
                error_message=run.stderr or "resolver timeout",
                meta={"url": url},
            )

        if run.exit_code not in (0, None):
            return ResolveResult(
                ok=False,
                source_url="",
                title="",
                is_live=False,
                container_hint="unknown",
                error_code="E_RESOLVE_NONZERO_EXIT",
                error_message=run.stderr or "resolver non-zero exit",
                meta={"url": url, "exit_code": run.exit_code},
            )

        try:
            payload = json.loads(run.stdout)
        except json.JSONDecodeError:
            return ResolveResult(
                ok=False,
                source_url="",
                title="",
                is_live=False,
                container_hint="unknown",
                error_code="E_RESOLVE_NONZERO_EXIT",
                error_message="invalid ytdlp json output",
                meta={"url": url},
            )

        return parse_ytdlp_output(payload)
