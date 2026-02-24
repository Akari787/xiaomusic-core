# Playback Refactor v1 Regression Baseline

## 0. Regression Goal

- Freeze current playable capability as the refactor baseline.
- Run full A/B/C/D after every playback refactor v1 subtask and record results.
- If baseline is unstable, fix input samples before continuing refactor.

## 1. Environment Constraints (fill once)

- LAN endpoint: `<LAN_IP>:<PORT>`
- Speaker id: `<SPEAKER_ID>`
- Runtime mode: Docker / local / other
- Dependency snapshot: `yt-dlp --version`, `ffmpeg -version`
- Network profile (optional): home broadband / hotspot

## 2. Case Definitions (A/B/C/D)

All cases must satisfy the requirement section, otherwise sample is invalid.

### Case A (YouTube VOD)

- URL placeholder: `<YOUTUBE_VOD_URL>`
- Requirement: public, no age-gate, 3-10 min, no strong geo lock.
- Steps:
  1. Call `play_url(<YOUTUBE_VOD_URL>, <SPEAKER_ID>, options)` (or compatible wrapper).
  2. Verify speaker starts output within N seconds.
  3. Call stop.
  4. Repeat play/stop 3 times.
- Pass criteria:
  - N=30s output starts.
  - stop works.
  - 3 repeats without crash/hang.

### Case B (YouTube Live)

- URL placeholder: `<YOUTUBE_LIVE_URL>`
- Requirement: currently live, long-running channel, low geo restriction.
- Steps: same as Case A.
- Pass criteria:
  - N=30s output starts.
  - stop works.
  - Optional: one auto-recover after short stream interruption (if supported).

### Case C (bilibili Live)

- URL placeholder: `<BILIBILI_LIVE_URL>`
- Requirement: currently live, no paid-only room, auto quality negotiation available.
- Steps: same as Case A.
- Pass criteria: same as Case A.

### Case D (Direct stream URL)

- URL placeholder: `<DIRECT_STREAM_URL>`
- Requirement: m3u8 or mp3 direct URL, stable response, playable for >= 2 minutes.
- Steps:
  1. Call play_url.
  2. Keep playing for 2 minutes.
  3. Call stop.
- Pass criteria:
  - N=15s output starts.
  - Plays 2 minutes without interruption (or one short reconnect if expected).
  - stop works.

## 3. fail_stage enum

Use only one of:

- `resolve`: URL resolve failed/timeout.
- `stream`: local stream endpoint failed (`/stream` no output / client connect failed).
- `ffmpeg`: transcode/extract failed, process crash, cannot spawn.
- `xiaomi`: push/playback on speaker failed or no audio.
- `unknown`: cannot classify (must include key logs in notes).

## 4. Regression Record Template

Field rule:

- `date/time`: `YYYY-MM-DD HH:mm:ss`
- `case`: only `A/B/C/D`
- `result`: only `pass` or `fail`
- `fail_stage`: keep empty for pass
- `notes`: latency, key logs, retry count, external fluctuation

| date/time           | commit | case | result | fail_stage | notes |
| ------------------- | ------ | ---- | ------ | ---------- | ----- |
| YYYY-MM-DD HH:mm:ss | `<SHA>`  | A    | pass   |            | t=12s; retries=0; keylog=...; ext_var=no |
| YYYY-MM-DD HH:mm:ss | `<SHA>`  | B    | fail   | resolve    | t=30s; retries=2; keylog=timeout; ext_var=live_unstable |
| YYYY-MM-DD HH:mm:ss | `<SHA>`  | C    | pass   |            | t=18s; retries=1; keylog=reconnect; ext_var=yes |
| YYYY-MM-DD HH:mm:ss | `<SHA>`  | D    | pass   |            | t=6s; retries=0; keylog=ok; ext_var=no |

## 5. Test Server Baseline Record

Environment:

- target: `192.168.7.178:58090`
- service: `xiaomusic-oauth2` image `akari787/xiaomusic-oauth2:v1.0.3`
- speaker_id: `981257654`
- note: on this speaker firmware, `getplayerstatus.status=2` is treated as stopped (non-playing)

| date/time           | commit             | case | result | fail_stage | notes |
| ------------------- | ------------------ | ---- | ------ | ---------- | ----- |
| 2026-02-24 21:24:21 | `v1.0.3-test-server` | A    | pass   |            | i1:t=4.02s stop_status=2; i2:t=3.82s stop_status=2; i3:t=8.66s stop_status=2 |
| 2026-02-24 21:25:20 | `v1.0.3-test-server` | B    | pass   |            | i1:t=4.15s stop_status=2; i2:t=3.57s stop_status=2; i3:t=4.23s stop_status=2 |
| 2026-02-24 21:26:29 | `v1.0.3-test-server` | C    | pass   |            | i1:t=6.38s stop_status=2; i2:t=6.17s stop_status=2; i3:t=4.67s stop_status=2 |
| 2026-02-24 21:28:39 | `v1.0.3-test-server` | D    | pass   |            | t=0.96s; samples=10s:1|30s:1|60s:1|90s:1|120s:1; stop_status=2 |
