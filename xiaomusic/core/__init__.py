"""Core orchestration skeleton for unified playback architecture."""

from xiaomusic.core.coordinator import PlaybackCoordinator
from xiaomusic.core.delivery import DeliveryAdapter
from xiaomusic.core.device import DeviceRegistry
from xiaomusic.core.source import SourceRegistry
from xiaomusic.core.transport import TransportPolicy, TransportRouter
