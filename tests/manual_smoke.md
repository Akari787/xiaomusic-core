# Manual Smoke Test (Test Server 192.168.7.178)

This is a non-destructive functional validation checklist.

## 0) Prereqs

- You have SSH access to `root@192.168.7.178`.
- Docker + docker compose are installed on the server.
- You have a prepared music folder with at least one playable file.

## 1) Deploy (docker-compose.hardened.yml)

On your dev machine:

```bash
# Copy repo to server (recommended: tarball or git clone)
scp -i ~/.ssh/id_ed25519_opencode -o IdentitiesOnly=yes -r . root@192.168.7.178:/root/xiaomusic_oauth2
```

On the server:

```bash
cd /root/xiaomusic_oauth2
mkdir -p conf music

# Start
docker compose -f docker-compose.hardened.yml up -d --build

# Verify
curl -fsS http://127.0.0.1:58090/getversion
```

Expected: version JSON (e.g. `{"version":"1.0.1"}`)

## 2) Startup Self-Check

```bash
curl -fsS http://127.0.0.1:58090/diagnostics | jq
```

Check:
- `startup.ok` is `true`
- `paths[]` show readable/writable where needed
- `tools[]` show `ffmpeg`/`ffprobe` found

If not ok, follow `startup.notes` suggestions (volume mounts, ffmpeg path, etc.).

## 3) Play a Local Song

Prepare a known file under the mounted `music/` dir.

Then use Web UI or API/ctrl-panel to trigger a local play command.

Quick check (API may differ by your UI):
- Use the UI to pick a track and start playback
- Confirm device plays audio

## 4) Exec (Default Disabled)

Attempt an exec command via control panel (or API):
- Example payload: `exec#http_get("https://example.com")`

Expected:
- HTTP 403
- No side effects

## 5) Enable Exec + Allowlist (http_get)

Edit your `conf/setting.json` (or your configured config file) and set:

```json
{
  "enable_exec_plugin": true,
  "allowed_exec_commands": ["http_get"],
  "outbound_allowlist_domains": ["example.com"]
}
```

Restart container:

```bash
docker compose -f docker-compose.hardened.yml restart
```

Now validate:
- `http_get("https://example.com")` should succeed
- `http_get("http://127.0.0.1")` and `http_get("http://192.168.1.1")` must be blocked

## 6) File Watch Refresh Coalescing

Enable file watch in config:

```json
{
  "enable_file_watch": true,
  "file_watch_debounce": 10
}
```

Then copy multiple files into the music folder quickly:

```bash
cp /path/to/some.mp3 /root/xiaomusic_oauth2/music/
cp /path/to/some2.mp3 /root/xiaomusic_oauth2/music/
```

Expected:
- Only a single refresh runs per debounce window (check container logs)

```bash
docker logs --tail 200 xiaomusic-oauth2 | rg "library refresh"
```

## 7) Playback Failure Backoff + Degraded Mode

Simulate a non-playable target (e.g. an invalid URL or missing file) and trigger playback.

Expected:
- Retry with exponential backoff
- After repeated failures, auto-next stops and a single user-facing TTS is emitted

## 8) Jellyfin Auto Proxy Fallback

If you use Jellyfin URLs that the speaker cannot reach directly:

- Configure Jellyfin (base url + api key) and enable Jellyfin
- Start playing a Jellyfin track

Expected:
- If direct URL is reachable by the speaker, it plays directly
- Otherwise XiaoMusic retries once via `/proxy/...` automatically
- Settings UI does not expose a manual proxy mode toggle
