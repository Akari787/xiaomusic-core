import base64
import hashlib
import logging
import os
from urllib import parse


def _enable_miservice_debug() -> None:
    if os.getenv("XIAOMUSIC_ENABLE_MISERVICE_DEBUG", "false").lower() != "true":
        return

    try:
        from miservice import MiAccount
    except Exception:
        logging.getLogger(__name__).exception(
            "failed to import miservice for debug patch"
        )
        return

    logger = logging.getLogger("miservice")

    async def _debug_security_token_service(self, location, nonce, ssecurity):
        nsec = "nonce=" + str(nonce) + "&" + ssecurity
        client_sign = base64.b64encode(hashlib.sha1(nsec.encode()).digest()).decode()
        request_url = location + "&clientSign=" + parse.quote(client_sign)
        location_redacted = parse.urlsplit(location)
        request_redacted = parse.urlsplit(request_url)
        logger.info(
            "miservice_securityTokenService request host=%s path=%s nonce_empty=%s ssecurity_empty=%s request_has_clientSign=%s location_has_query=%s",
            location_redacted.netloc,
            location_redacted.path,
            not bool(nonce),
            not bool(ssecurity),
            "clientSign=" in request_url,
            bool(location_redacted.query),
        )
        async with self.session.get(request_url) as r:
            cookie_keys = list(r.cookies.keys())
            response_header_names = sorted(
                key.lower() for key in r.headers
                if key.lower() in {"content-type", "location", "set-cookie", "server", "www-authenticate"}
            )
            logger.info(
                "miservice_securityTokenService response status=%s host=%s path=%s cookie_keys=%s serviceToken_present=%s passToken_present=%s sensitive_header_names=%s",
                r.status,
                request_redacted.netloc,
                request_redacted.path,
                cookie_keys,
                "serviceToken" in r.cookies,
                "passToken" in r.cookies,
                response_header_names,
            )
            try:
                service_token = r.cookies["serviceToken"].value
            except KeyError:
                body = await r.text()
                logger.info(
                    "miservice_securityTokenService missing_serviceToken body_len=%s",
                    len(body),
                )
                raise RuntimeError("service_token_cookie_missing") from None
            if not service_token:
                body = await r.text()
                logger.info(
                    "miservice_securityTokenService empty_serviceToken body_len=%s",
                    len(body),
                )
                raise RuntimeError("service_token_cookie_empty")
        return service_token

    MiAccount._securityTokenService = _debug_security_token_service
    logger.info("miservice_securityTokenService debug patch enabled")


_enable_miservice_debug()


def _enable_miaccount_cookie_observation() -> None:
    """Observe cookie rotation using in-memory comparison; log only safe metadata."""
    if os.getenv("XIAOMUSIC_ENABLE_MICOOKIE_OBSERVE", "false").lower() != "true":
        return

    try:
        import hashlib

        from aiohttp import ClientSession
    except Exception:
        logging.getLogger(__name__).warning(
            "failed to import aiohttp for cookie observation"
        )
        return

    logger = logging.getLogger("xiaomusic.cookie_obs")
    original_request = getattr(ClientSession, "_request", None)
    if original_request is None:
        logger.warning(
            "aiohttp ClientSession._request missing; cookie observation disabled"
        )
        return

    seen: dict[str, str] = {}
    counters = {"total": 0, "with_pass_token": 0, "rotations": 0}

    def _fingerprint(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8", "ignore")).hexdigest()[:8]

    def _record(resp, request_url) -> None:
        counters["total"] += 1
        cookies = getattr(resp, "cookies", None)
        if not cookies or "passToken" not in cookies:
            return
        value = cookies["passToken"].value
        if not isinstance(value, str) or not value:
            return
        counters["with_pass_token"] += 1
        digest = _fingerprint(value)
        try:
            host = (getattr(resp, "url", None) or request_url).host or ""
        except Exception:
            host = ""
        previous = seen.get(host)
        rotated = bool(previous and previous != digest)
        if rotated:
            counters["rotations"] += 1
        seen[host] = digest
        logger.info(
            "micoookie_set passToken host_class=%s status=%s rotated=%s "
            "total=%s with_token=%s rotations=%s",
            "account" if host == "account.xiaomi.com" or host.endswith(".account.xiaomi.com") else "other",
            getattr(resp, "status", 0) if isinstance(getattr(resp, "status", None), int) else 0,
            rotated,
            counters["total"],
            counters["with_pass_token"],
            counters["rotations"],
        )

    async def _observed_request(self, method, str_or_url, **kwargs):
        resp = await original_request(self, method, str_or_url, **kwargs)
        try:
            _record(resp, str_or_url)
        except Exception:
            logger.debug("cookie observation failed")
        return resp

    ClientSession._request = _observed_request
    logger.info("micoookie observation enabled")


_enable_miaccount_cookie_observation()
