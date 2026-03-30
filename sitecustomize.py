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
    original = MiAccount._securityTokenService

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
            response_headers = {
                key: r.headers.get(key)
                for key in (
                    "Content-Type",
                    "Location",
                    "Set-Cookie",
                    "Server",
                    "WWW-Authenticate",
                )
                if r.headers.get(key) is not None
            }
            logger.info(
                "miservice_securityTokenService response status=%s host=%s path=%s cookie_keys=%s serviceToken_present=%s headers=%s",
                r.status,
                request_redacted.netloc,
                request_redacted.path,
                cookie_keys,
                "serviceToken" in r.cookies,
                response_headers,
            )
            try:
                service_token = r.cookies["serviceToken"].value
            except KeyError:
                body = await r.text()
                logger.info(
                    "miservice_securityTokenService missing_serviceToken body_len=%s body_preview=%s",
                    len(body),
                    body[:500],
                )
                raise
            if not service_token:
                body = await r.text()
                logger.info(
                    "miservice_securityTokenService empty_serviceToken body_len=%s body_preview=%s",
                    len(body),
                    body[:500],
                )
                raise Exception(body)
        return service_token

    MiAccount._securityTokenService = _debug_security_token_service
    logger.info("miservice_securityTokenService debug patch enabled")


_enable_miservice_debug()
