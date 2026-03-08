from __future__ import annotations

from xiaomusic.constants.api_fields import DEVICE_ID, OPTIONS, QUERY, REQUEST_ID, SOURCE_HINT

# Shared payload/context key constants for playback boundary parsing.

KEY_DEVICE_ID = DEVICE_ID
KEY_QUERY = QUERY
KEY_SOURCE_HINT = SOURCE_HINT
KEY_OPTIONS = OPTIONS
KEY_REQUEST_ID = REQUEST_ID

KEY_SPEAKER_ID = "speaker_id"

OPT_RESOLVE_TIMEOUT_SECONDS = "resolve_timeout_seconds"
OPT_TIMEOUT = "timeout"
OPT_START_POSITION = "start_position"
OPT_SHUFFLE = "shuffle"
OPT_LOOP = "loop"
OPT_VOLUME = "volume"
OPT_NO_CACHE = "no_cache"
OPT_PREFER_PROXY = "prefer_proxy"
OPT_CONFIRM_START = "confirm_start"
OPT_CONFIRM_START_DELAY_MS = "confirm_start_delay_ms"
OPT_CONFIRM_START_RETRIES = "confirm_start_retries"
OPT_CONFIRM_START_INTERVAL_MS = "confirm_start_interval_ms"
OPT_SOURCE_PAYLOAD = "source_payload"
OPT_MEDIA_ID = "media_id"
OPT_ID = "id"
OPT_TITLE = "title"

PAYLOAD_SOURCE = "source"
PAYLOAD_URL = "url"
PAYLOAD_ID = "id"
PAYLOAD_TITLE = "title"
PAYLOAD_MUSIC_NAME = "music_name"
PAYLOAD_TRACK_ID = "track_id"
PAYLOAD_PATH = "path"
PAYLOAD_NAME = "name"
