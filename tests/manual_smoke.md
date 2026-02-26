# Manual Smoke Test (<TEST_SERVER_HOST>)

目标：在测试服务器 `<TEST_SERVER_HOST>` 上用 hardened compose 做一次非破坏性验证。

## 1) 启动（docker-compose.hardened.yml）

在服务器上：

```bash
cd /root/xiaomusic_oauth2
mkdir -p conf music

docker compose -f docker-compose.hardened.yml up -d --build

curl -fsS http://127.0.0.1:58090/getversion
curl -fsS http://<TEST_SERVER_HOST>:58090/getversion
```

期望：两次都返回版本 JSON，且外部可通过 `http://<TEST_SERVER_HOST>:58090` 访问。

## 2) CORS 默认仅 localhost

```bash
curl -s -D - -o /dev/null \
  -H "Origin: http://evil.com" \
  -H "Access-Control-Request-Method: GET" \
  -X OPTIONS http://127.0.0.1:58090/getversion | grep -i access-control-allow-origin || true

curl -s -D - -o /dev/null \
  -H "Origin: http://localhost" \
  -H "Access-Control-Request-Method: GET" \
  -X OPTIONS http://127.0.0.1:58090/getversion | grep -i access-control-allow-origin || true
```

期望：
- evil.com 不应出现 `Access-Control-Allow-Origin`
- localhost 应允许（返回 `Access-Control-Allow-Origin: http://localhost`

## 3) Exec 默认禁用

先获取一个 DID（可从设置接口返回的 device_list 中选择）：

```bash
curl -fsS "http://127.0.0.1:58090/getsetting?need_device_list=true" | head
```

然后通过 API 触发（把 `<DID>` 替换成实际 did）：

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  -X POST http://127.0.0.1:58090/cmd \
  -H "Content-Type: application/json" \
  -d '{"did":"<DID>","cmd":"exec#http_get(\\\"https://example.com\\\")"}'
```

期望：HTTP 403（默认禁用危险能力）。

## 4) 开启 exec + allowlist 后验证 http_get

编辑 `conf/setting.json`：

```json
{
  "enable_exec_plugin": true,
  "allowed_exec_commands": ["http_get"],
  "outbound_allowlist_domains": ["example.com"]
}
```

重启：

```bash
docker compose -f docker-compose.hardened.yml restart
```

验证：
- `http_get("https://example.com")` 成功
- `http_get("http://127.0.0.1")` / `http_get("http://192.168.7.1")` 必须拒绝

## 5) 自更新默认拒绝 + 安全解压验证

默认 `enable_self_update=false`：调用更新接口应拒绝。

开启后再验证：

```json
{
  "enable_self_update": true,
  "outbound_allowlist_domains": ["gproxy.hanxi.cc", "github.com"]
}
```

期望：
- enable_self_update=false 时，updateversion 拒绝
- enable_self_update=true 且 allowlist 配置正确时，可以正常更新
- 恶意 tar（包含 `../`、绝对路径、symlink）不会写到目标目录外

验证方法示例：

```bash
find /app -maxdepth 2 -name pwn.txt || true
```

## 6) 如何定位 outbound 失败原因（不含敏感信息）

```bash
docker logs --tail 300 xiaomusic-oauth2 | grep -i -E "SECURITY:|outbound|blocked" || true
```

期望：能看到被拦截原因（domain not allowlisted / ip literal not allowed / resolved to private 等），且日志会脱敏。
