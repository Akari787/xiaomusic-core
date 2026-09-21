# Security Policy

## Reporting a Vulnerability

Please do NOT open a public issue for security-sensitive reports.

- Preferred: open a GitHub issue with the label `security` and include only non-sensitive details.
- If the report includes credentials, tokens, private IPs, or logs, redact them first.

## What to Include

- XiaoMusic version
- Deployment method (Docker / bare metal)
- Minimal steps to reproduce
- Relevant redacted logs

## HTTP Client Redirect Headers

The root Node runtime lockfile uses `follow-redirects` 1.16.0. Upstream commit
`844c4d3` adds the `options.sensitiveHeaders` allowlist for caller-defined
sensitive headers. It does **not** automatically strip every custom header on a
cross-origin redirect: the default policy covers `Authorization`,
`Proxy-Authorization`, and `Cookie`.

Callers that send credentials in custom headers (for example, `X-API-Key`) are
responsible for passing `sensitiveHeaders: ['X-API-Key']`. The JS plugin system
currently has no unified Axios/redirect wrapper, so this release records the
requirement in the security gate and does not make a broad plugin-system change.
