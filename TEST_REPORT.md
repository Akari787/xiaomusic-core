# TEST REPORT

## Execution

- Date: 2026-02-28
- Environment: test server `192.168.7.178`
- Image: `akari787/xiaomusic-oauth2:v1.0.5`
- Scope: security hardening + external degrade + tag cache incremental + module split compatibility

## Deployment Steps

1. Synced latest `oauth2-only` changes to `/root/xiaomusic_oauth2_smoke`
2. Built image: `docker build -t akari787/xiaomusic-oauth2:v1.0.5 /root/xiaomusic_oauth2_smoke`
3. Deployed: `docker compose -f docker-compose.hardened.yml up -d --force-recreate`
4. Verified version endpoint: `{"version":"1.0.5"}`

## Functional Checks

- Secrets injection at startup
  - Added required env: `API_SECRET`, `HTTP_AUTH_HASH`
  - Service starts only after env provided
- OAuth status and token persistence
  - Before restart: `/api/oauth2/status` -> `token_valid=true`
  - After container restart: `/api/oauth2/status` -> `token_valid=true`
- QR login
  - `/api/get_qrcode` returns `success=true` and non-empty `qrcode_url`
- Playback control
  - `/api/v1/play_url` -> `ok=true, state=streaming`
  - `/api/v1/status` -> `ok=true, stage=stream`
  - `/api/v1/stop` -> `ok=true, state=stopped`
- External API unavailable simulation
  - Forced invalid MiJia service URL
  - Got structured degrade error: `E_EXTERNAL_SERVICE_UNAVAILABLE` with `error_id`
- Tag cache rebuild
  - Called `/refreshmusictag`
  - Benchmark log observed: `tag 更新完成 benchmark scanned=... refreshed=... skipped=... cost_ms=...`

## Security / Log Leakage Check

- Searched runtime logs for `API_SECRET`, `HTTP_AUTH_HASH`, bcrypt prefix `$2b$`
- No secret leakage found

## Stability Soak

- Duration: 30 minutes
- Log scan keywords: `Traceback`, `ERROR`, `Exception`
- Result: no fatal stack trace/error signatures found during soak window

## Performance Comparison (Tag Cache)

- Before:
  - `refresh_music_tag` clears cache and triggers full rebuild path
  - No incremental skip metric
- After:
  - mtime+size signature incremental refresh
  - Benchmark output includes `scanned/refreshed/skipped/cost_ms`
  - On current test data logs show near-zero rebuild overhead (`cost_ms` ~ 0-1ms)

## Result

- Overall: PASS
- Blocking issues: none observed in this validation round

## Coverage Scope Note

- This stabilization patch follows the "minimal-change" policy.
- Acceptance coverage is evaluated on newly added/modified paths and their regression suites (all passed).
- Full-repository `--cov=xiaomusic` baseline remains legacy-wide and is not used as this patch gate.
