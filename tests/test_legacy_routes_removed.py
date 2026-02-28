import pytest

pytest.importorskip("aiofiles")

from xiaomusic.api.routers import network_audio


def _route_keys():
    rows = set()
    for route in network_audio.router.routes:
        methods = route.methods or set()
        for method in methods:
            rows.add((method.upper(), route.path))
    return rows


def test_legacy_control_routes_removed_from_network_audio_router():
    routes = _route_keys()
    assert ("POST", "/network_audio/play_url") not in routes
    assert ("POST", "/network_audio/play_link") not in routes
    assert ("POST", "/network_audio/stop") not in routes


def test_network_audio_stream_route_kept():
    routes = _route_keys()
    assert ("GET", "/network_audio/stream/{sid}") in routes


def test_network_audio_observability_routes_kept():
    routes = _route_keys()
    assert ("GET", "/network_audio/healthz") in routes
    assert ("GET", "/network_audio/sessions") in routes
