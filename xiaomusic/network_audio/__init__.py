"""Legacy alias: xiaomusic.network_audio is deprecated, use xiaomusic.relay instead.

This module re-exports from xiaomusic.relay for backward compatibility.
New code should import from xiaomusic.relay directly.
"""

import sys
from importlib import import_module

_submodules = [
    "audio_streamer",
    "api",
    "contracts",
    "fake_source_server",
    "local_http_stream_server",
    "play_service",
    "reconnect_policy",
    "resolver",
    "resolver_cache",
    "runtime",
    "session_manager",
    "url_classifier",
    "xiaomi_adapter",
    "ytdlp_parser",
    "ytdlp_runner",
]

for _name in _submodules:
    _mod = import_module(f"xiaomusic.relay.{_name}")
    globals()[_name] = _mod
    sys.modules[f"xiaomusic.network_audio.{_name}"] = _mod

relay = import_module("xiaomusic.relay")
sys.modules["xiaomusic.network_audio.relay"] = relay

from xiaomusic.relay.runtime import RelayRuntime
from xiaomusic.relay.play_service import RelayPlayService

NetworkAudioRuntime = RelayRuntime
NetworkAudioPlayService = RelayPlayService

sys.modules["xiaomusic.network_audio"].Runtime = RelayRuntime
sys.modules["xiaomusic.network_audio"].NetworkAudioRuntime = RelayRuntime
sys.modules["xiaomusic.network_audio"].NetworkAudioPlayService = RelayPlayService
