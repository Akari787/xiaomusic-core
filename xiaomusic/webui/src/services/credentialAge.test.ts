import { describe, expect, it } from "vitest";
import type { AuthStatus } from "./auth";
import { credentialAgeText } from "./credentialAge";

describe("credential age information", () => {
  it.each([59, 60, 380])("shows %s hours without an expiry warning", (hours) => {
    const text = credentialAgeText({ pass_token_age_seconds: hours * 3600 } as AuthStatus);
    expect(text).toContain("年龄仅供参考");
    expect(text).not.toContain("建议重新扫码");
    expect(text).not.toContain("失效风险");
  });
  it.each(["unknown_age", "approaching_expiry"] as const)("treats %s as neutral information", (warning) => {
    expect(credentialAgeText({ credential_warning: warning } as AuthStatus)).toContain("签发时间未知");
  });
  it("does not fabricate an age", () => {
    expect(credentialAgeText({ pass_token_age_seconds: Number.NaN } as AuthStatus)).toBe("");
  });
});
