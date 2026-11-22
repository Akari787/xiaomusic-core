import type { AuthStatus } from "./auth";

export function credentialAgeText(status?: AuthStatus | null): string {
  const age = status?.pass_token_age_seconds;
  if (typeof age === "number" && Number.isFinite(age) && age >= 0) {
    const hours = Math.floor(age / 3600);
    const duration = hours >= 24 ? `${Math.floor(hours / 24)} 天 ${hours % 24} 小时` : `${hours} 小时`;
    return `长期凭据年龄：${duration}。年龄仅供参考，不代表到期时间。`;
  }
  // Old servers may still send approaching_expiry; it is not a failure signal.
  if (status?.credential_warning === "unknown_age" || status?.credential_warning === "approaching_expiry") {
    return "长期凭据签发时间未知；无需仅因年龄未知而扫码。";
  }
  return "";
}
