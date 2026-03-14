# Phase 2 Acceptance Report

## 1. Test Time

- Date: 2026-03-07
- Time window (UTC+8): 12:00 - 12:15

## 2. Test Server Environment

- Server: `root@192.168.7.178` (hostname: `test`)
- Deploy path: `/root/xiaomusic_core_smoke` (historical path: `/root/xiaomusic_oauth2_smoke`)
- Runtime: Docker Compose (`docker-compose.hardened.yml`)
- Container: `xiaomusic-core` (historical container: `xiaomusic-oauth2`)
- Image: `xiaomusic-core:latest` (historical image: `xiaomusic:oauth2-only`)
- Service port: `58090 -> 8090`

## 3. Deployment Branch / Commit

- Branch: `core-main` (historical working branch: `oauth2-only`)
- Base commit: `c3375bfd897a68854e9b651ac17539b8cb4815c5`
- Note: Phase 2 changes were deployed from current workspace updates on top of the base commit.

## 4. Acceptance Device

- Speaker DID: `981257654`
- Device name: Xiaomi Smart Speaker Pro
- Hardware: `OH2P`

## 5. Jellyfin Real Device Playback

## 5.1 Test Method

- Query online source list: `GET /api/search/online?keyword=love&plugin=all&page=1&limit=20`
- Picked item with `source=jellyfin`
- Trigger playback: `POST /api/device/pushUrl`

## 5.2 Result

- Playback request returned success:
  - `ok=true`
  - `mode=core_minimal`
  - `source=jellyfin`
  - `transport=mina`
- Speaker started playback (legacy cast log exists and no playback regression observed).
- Unified-chain log evidence:
  - `core_chain action=play ... source_hint=legacy_payload plugin=legacy_payload`

## 6. http_url Real Device Playback

## 6.1 Test Method

- API: `POST /api/v1/play_url`
- URL: `http://192.168.7.178:58090/static/silence.mp3`

## 6.2 Result

- API returned:
  - `ok=true`
  - `state=streaming`
- Unified-chain log evidence:
  - `core_chain action=play ... source_hint=http_url plugin=http_url`
- Delivery path confirmed:
  - `HttpUrlSourcePlugin -> DeliveryAdapter -> TransportRouter -> MinaTransport`

## 7. stop / pause / tts / set_volume / probe Acceptance

## 7.1 Commands

- `POST /api/v1/set_volume` (`volume=42`)
- `POST /api/v1/tts`
- `POST /api/v1/probe`
- `POST /api/v1/pause`
- `POST /api/v1/stop`

## 7.2 Results

- `set_volume`: success (`ok=true`), device volume reflected to `42`.
- `tts`: success (`ok=true`), speaker played TTS.
- `probe`: success (`ok=true`), returned `transport=miio` and reachability snapshot.
- `pause`: success (`ok=true`).
- `stop`: success (`ok=true`, state `stopped`).

## 7.3 Probe + DeviceRegistry Verification

- Repeated probe calls showed `last_probe_ts` increasing (`1772856414 -> 1772856416`).
- This confirms probe result is written through `DeviceRegistry.update_reachability()`.
- Transport adapters only return probe result, they do not write device reachability state.

## 8. Unfinished Items and Known Limits

- `MiioTransport.play_url` is still a compatibility placeholder:
  - It delegates to legacy `xiaomusic.play_url` path.
  - It is explicitly marked in adapter code as non-standalone local Miio streaming implementation.
- Jellyfin source is currently routed via `LegacyPayloadSourcePlugin` compatibility layer.
  - This is the migration bridge for future `JellyfinSourcePlugin` extraction.
- Pause status semantics depend on device firmware (`getplayerstatus` observed `status=2` after pause/stop sequence).

## 9. Next Phase Recommendations

1. Replace `LegacyPayloadSourcePlugin` by dedicated `JellyfinSourcePlugin` and migrate payload mapping logic out of compatibility resolver.
2. Implement true local Miio media playback path (or remove Miio play fallback for `play` until fully implemented).
3. Add core-level action metrics (`action`, `transport`, `success/failure`) for stable regression tracking.
4. Expand automated integration tests for `/api/v1/pause|tts|set_volume|probe` response shape and fallback behavior.
