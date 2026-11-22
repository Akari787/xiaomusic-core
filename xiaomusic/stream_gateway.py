"""StreamGateway: stable, signed stream URLs for the speaker (ADR-0007 step 1).

Every track handed to the speaker gets an address of the form

    /stream/v1/<did>/<queue>/<index>/<source>/<item>?sig=<hmac>

The path carries everything needed to find the audio again (source kind and
item id), so the address keeps working after a restart, unlike the in-memory
``/proxy`` tokens. The HMAC covers every path field and the key lives in
``conf/``, so the gateway only serves addresses it issued itself and cannot be
used as an open proxy.

When the speaker fetches an address, several HTTP requests usually arrive for
the same track within a fraction of a second (open + abort, a tail read for
ID3 tags, the real download). ``FetchTracker`` folds them into one
``track_fetched`` event. In step 1 the event is only logged; later steps let
the playback follower act on it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, unquote, urlparse

SOURCE_LOCAL = "local"
SOURCE_JELLYFIN = "jellyfin"
SOURCE_WEB = "web"
SOURCES = frozenset({SOURCE_LOCAL, SOURCE_JELLYFIN, SOURCE_WEB})

KEY_FILE = "stream_gateway.key"
SIG_BYTES = 16
FETCH_WINDOW_SEC = 3.0

_JELLYFIN_ITEM_RE = re.compile(r"/Audio/([0-9A-Za-z-]+)/")
_RANGE_START_RE = re.compile(r"^\s*bytes\s*=\s*(\d*)\s*-")


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    pad = (-len(text)) % 4
    return base64.urlsafe_b64decode(text + "=" * pad)


def encode_item(item: str) -> str:
    return _b64e(item.encode("utf-8"))


def decode_item(token: str) -> str:
    return _b64d(token).decode("utf-8")


@dataclass(frozen=True)
class StreamRef:
    did: str
    queue: str
    index: int
    source: str
    item: str  # decoded item: relative path, Jellyfin item id, or URL


class StreamSigner:
    """HMAC signer whose key is created once and kept in conf/."""

    def __init__(self, conf_path: str):
        self._conf_path = conf_path or "conf"
        self._key: bytes | None = None

    @property
    def key_path(self) -> str:
        return os.path.join(self._conf_path, KEY_FILE)

    def _load_key(self) -> bytes:
        if self._key is not None:
            return self._key
        path = self.key_path
        try:
            with open(path, encoding="ascii") as f:
                key = bytes.fromhex(f.read().strip())
            if len(key) >= 32:
                self._key = key
                return key
        except (FileNotFoundError, ValueError):
            pass
        os.makedirs(self._conf_path, exist_ok=True)
        key = secrets.token_bytes(32)
        tmp = f"{path}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(key.hex())
        os.replace(tmp, path)
        self._key = key
        return key

    @staticmethod
    def _message(did: str, queue: str, index: int, source: str, item_token: str) -> bytes:
        return "\n".join(["v1", did, queue, str(index), source, item_token]).encode("utf-8")

    def sign(self, did: str, queue: str, index: int, source: str, item_token: str) -> str:
        mac = hmac.new(
            self._load_key(),
            self._message(did, queue, index, source, item_token),
            hashlib.sha256,
        ).digest()
        return _b64e(mac[:SIG_BYTES])

    def verify(self, did: str, queue: str, index: int, source: str, item_token: str, sig: str) -> bool:
        expected = self.sign(did, queue, index, source, item_token)
        return hmac.compare_digest(expected, str(sig or ""))


def range_start(range_header: str | None) -> int | None:
    """Start offset of a ``Range: bytes=N-`` header; None when absent."""
    if not range_header:
        return None
    m = _RANGE_START_RE.match(range_header)
    if not m:
        return None
    if m.group(1) == "":
        return -1  # suffix range ("bytes=-128"): a tail read
    return int(m.group(1))


class FetchTracker:
    """Folds the speaker's request burst for one track into one event."""

    def __init__(
        self,
        log,
        *,
        window_sec: float = FETCH_WINDOW_SEC,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.log = log
        self.window_sec = window_sec
        self._clock = clock
        self._last_request: dict[tuple[str, str, int], float] = {}
        self.last_fetch: dict[str, dict] = {}

    def observe(self, ref: StreamRef, range_header: str | None) -> bool:
        """Return True when this request starts a new fetch of the track."""
        start = range_start(range_header)
        if start not in (None, 0):
            # Tail reads (ID3 probing) and mid-file resumes are not a switch.
            return False
        key = (ref.did, ref.queue, ref.index)
        now = self._clock()
        last = self._last_request.get(key)
        self._last_request[key] = now
        if last is not None and now - last < self.window_sec:
            return False
        self._prune(now)
        self.last_fetch[ref.did] = {
            "queue": ref.queue,
            "index": ref.index,
            "source": ref.source,
            "at": time.time(),
        }
        self.log.info(
            "track_fetched did=%s queue=%s index=%s source=%s",
            ref.did,
            ref.queue,
            ref.index,
            ref.source,
        )
        return True

    def _prune(self, now: float) -> None:
        if len(self._last_request) < 256:
            return
        horizon = now - 60.0
        for key in [k for k, t in self._last_request.items() if t < horizon]:
            self._last_request.pop(key, None)


@dataclass(frozen=True)
class LocalTarget:
    relative_path: str


@dataclass(frozen=True)
class HttpTarget:
    url: str


class StreamGateway:
    def __init__(
        self,
        config,
        log,
        *,
        music_library_getter: Callable[[], object] | None = None,
        jellyfin_client_getter: Callable[[], object] | None = None,
    ):
        self.config = config
        self.log = log
        self.signer = StreamSigner(getattr(config, "conf_path", "conf"))
        self.tracker = FetchTracker(log)
        self._music_library = music_library_getter or (lambda: None)
        self._jellyfin_client = jellyfin_client_getter or (lambda: None)

    # ── issuing addresses ───────────────────────────────────────────────

    def _base(self) -> str:
        return str(self.config.get_public_base_url() or "").rstrip("/")

    def classify(self, url: str) -> tuple[str, str] | None:
        """Map a playable URL to (source, item), or None to leave it alone."""
        url = str(url or "").strip()
        if not url:
            return None
        base = self._base()
        if base and url.startswith(base + "/"):
            path = urlparse(url).path
            if path.startswith("/music/"):
                return SOURCE_LOCAL, unquote(path[len("/music/"):])
            if path in ("/proxy/music", "/proxy"):
                token = (parse_qs(urlparse(url).query).get("urlb64") or [""])[0]
                origin = self._resolve_proxy_token(token)
                return self.classify(origin) if origin else None
            # /relay/... (site_media live relay), /proxy/radio, static files:
            # not handled in step 1.
            return None
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return None
        if parsed.path.lower().endswith((".m3u8", ".m3u")):
            return None
        library = self._music_library()
        is_jellyfin = getattr(library, "is_jellyfin_url", None)
        if callable(is_jellyfin) and is_jellyfin(url):
            m = _JELLYFIN_ITEM_RE.search(parsed.path)
            if m:
                return SOURCE_JELLYFIN, m.group(1)
            return None
        return SOURCE_WEB, url

    def _resolve_proxy_token(self, token: str) -> str:
        if not str(token or "").startswith("t."):
            return ""
        library = self._music_library()
        resolver = getattr(library, "resolve_proxy_url_token", None)
        if not callable(resolver):
            return ""
        return str(resolver(token[2:]) or "")

    def url_for(self, did: str, queue: str, index: int, source: str, item: str) -> str:
        token = encode_item(item)
        sig = self.signer.sign(did, queue, index, source, token)
        path = "/".join(
            quote(str(part), safe="")
            for part in ("stream", "v1", did, queue, index, source, token)
        )
        return f"{self._base()}/{path}?sig={sig}"

    def wrap(self, url: str, *, did: str, queue: str, index: int) -> str:
        """Gateway address for ``url``; ``url`` itself when not handled."""
        if not self._base():
            return url
        classified = self.classify(url)
        if classified is None:
            return url
        source, item = classified
        return self.url_for(str(did), str(queue), max(int(index), 0), source, item)

    # ── serving addresses ───────────────────────────────────────────────

    def verify(
        self, did: str, queue: str, index: int, source: str, item_token: str, sig: str
    ) -> StreamRef | None:
        if source not in SOURCES:
            return None
        if not self.signer.verify(did, queue, index, source, item_token, sig):
            return None
        try:
            item = decode_item(item_token)
        except (ValueError, UnicodeDecodeError):
            return None
        return StreamRef(did=did, queue=queue, index=index, source=source, item=item)

    def resolve(self, ref: StreamRef) -> LocalTarget | HttpTarget:
        if ref.source == SOURCE_LOCAL:
            return LocalTarget(ref.item)
        if ref.source == SOURCE_JELLYFIN:
            client = self._jellyfin_client()
            stream_url = getattr(client, "_stream_url", None)
            if client is None or not callable(stream_url) or not client.enabled():
                raise LookupError("jellyfin not available")
            # Built with the current session token; the token never appears in
            # the address the speaker holds.
            return HttpTarget(stream_url(ref.item))
        return HttpTarget(ref.item)
