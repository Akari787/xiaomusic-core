from xiaomusic.api.routers import relay


def _route_keys():
    rows = set()
    for route in relay.router.routes:
        methods = route.methods or set()
        for method in methods:
            rows.add((method.upper(), route.path))
    return rows


def test_relay_control_routes_removed():
    routes = _route_keys()
    assert ("POST", "/relay/play_url") not in routes
    assert ("POST", "/relay/play_link") not in routes
    assert ("POST", "/relay/stop") not in routes


def test_relay_stream_route_exists():
    routes = _route_keys()
    assert ("GET", "/relay/stream/{sid}") in routes


def test_relay_observability_routes_exist():
    routes = _route_keys()
    assert ("GET", "/relay/healthz") in routes
    assert ("GET", "/relay/sessions") in routes
