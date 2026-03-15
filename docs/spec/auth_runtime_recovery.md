# Auth Runtime Recovery Spec

> Last updated: 2026-03-12

## 1. Background

`xiaomusic-core` historically mixed two different actions in one path:

- refresh token from Xiaomi cloud
- reload runtime from local `auth.json`

In production recovery, automatic `mi_account.login("micoapi")` frequently hit Xiaomi risk control (`70016`) and failed to produce usable short-lived tokens. This made automatic fallback unpredictable and amplified outages.

This stable release closes the recovery scope and makes recovery chain deterministic.

## 2. Three-layer auth model

- Long-lived auth state (persisted): `passToken`, `psecurity`, `ssecurity`, `userId`, `cUserId`, `deviceId`
- Short-lived session state (persisted): `serviceToken`, `yetAnotherServiceToken`
- Runtime state (in-memory): `MiAccount` token seed, `mina_service`, `miio_service`, runtime device map

Source of truth is always `auth.json` / `TokenStore`.

## 3. Why automatic login fallback is disabled

- Server-side auto fallback `mi_account.login("micoapi")` is not reliable in recovery windows.
- Typical failure is Xiaomi `70016` and no new short-lived token writeback.
- Therefore this call is policy-disabled for automatic recovery.
- When fallback branch is encountered, logs must include `disabled_by_policy=true`.

## 4. Standard recovery chains

### 4.1 Short-session invalid recovery (automatic)

Only allowed sequence:

1. clear short session (`serviceToken`, `yetAnotherServiceToken`)
2. rebuild short session from long-lived auth data
3. runtime rebind (`mina_service`, `miio_service`)
4. verify (`device_list` / runtime auth ready)

No implicit alternative branch is allowed.

### 4.2 QR login handover recovery (manual)

When user completes QR login and new short tokens are persisted to disk:

1. call `POST /api/auth/refresh` (or `POST /api/auth/refresh_runtime`)
2. runtime reload from disk
3. runtime rebind
4. device map refresh
5. verify

Container restart is not required.

## 5. Refresh vs Reload Runtime

- Refresh: cloud-side session refresh action.
- Refresh runtime: local runtime reload from disk; does not require cloud-side refresh success.

Current release behavior:

- `POST /api/auth/refresh` means refresh runtime
- `POST /api/auth/refresh_runtime` is explicit alias with same behavior

## 6. Locked policy

`auth locked` is a terminal protection state and only applies when long-lived auth is unavailable or equivalent hard-failure conditions are met.

Do not lock only because of:

- short-session invalidation
- policy-disabled auto login fallback
- single runtime verify failure

## 7. Observability

Runtime reload emits structured event `auth_runtime_reload` with at least:

- `stage=reload_runtime`
- `result`
- `token_store_reloaded`
- `disk_has_serviceToken`
- `disk_has_yetAnotherServiceToken`
- `runtime_seed_has_serviceToken`
- `mina_service_rebuilt`
- `miio_service_rebuilt`
- `device_map_refreshed`
- `verify_result`
- `error_code`
- `error_message`
- `refresh_token_path_invoked`

Debug endpoints:

- `GET /api/v1/debug/auth_state`
- `GET /api/v1/debug/auth_recovery_state`
- `GET /api/v1/debug/miaccount_login_trace`
- `GET /api/v1/debug/auth_runtime_reload_state`

## 8. Ops guidance

- Keep network path stable and low-variance.
- Avoid unnecessary proxy/protocol rewriting in Xiaomi auth traffic.
- Treat `auth.json` as source of truth; runtime should be rebuilt from disk after QR login.

## 9. Known boundaries

- Xiaomi server-side risk control cannot be fully eliminated.
- Goal is not zero failure; goal is low-frequency, recoverable, predictable behavior.
- API expansion (playlist/queue/library/object) is explicitly out of scope for this release.
