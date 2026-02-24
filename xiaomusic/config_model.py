from __future__ import annotations

import warnings
from typing import Any

from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError, field_validator


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    enable_exec_plugin: bool
    allowed_exec_commands: list[str]
    outbound_allowlist_domains: list[str]
    allowlist_domains: list[str] = []
    enable_self_update: bool
    cors_allow_origins: list[str]
    log_redact: bool
    jellyfin_base_url: str
    jellyfin_api_key: SecretStr
    port: int = 8090

    @field_validator(
        "allowed_exec_commands",
        "outbound_allowlist_domains",
        "allowlist_domains",
        "cors_allow_origins",
        mode="before",
    )
    @classmethod
    def _validate_list_str(cls, v: Any):
        if v is None:
            return []
        if not isinstance(v, list):
            raise TypeError("must be list[str]")
        return [str(x).strip() for x in v if str(x).strip()]

    @field_validator("outbound_allowlist_domains", "allowlist_domains", mode="after")
    @classmethod
    def _normalize_domains(cls, v: list[str]):
        return [x.lower() for x in v]


def validate_config_model(data: dict[str, Any], *, warn: bool = True) -> ConfigModel:
    normalized = dict(data)
    bool_fields = [
        "enable_exec_plugin",
        "enable_self_update",
        "log_redact",
    ]
    for key in bool_fields:
        value = normalized.get(key)
        if isinstance(value, str):
            lower = value.strip().lower()
            if lower in ("true", "false"):
                normalized[key] = lower == "true"
                if warn:
                    warnings.warn(
                        f"config field '{key}' accepted legacy string bool '{value}'",
                        stacklevel=2,
                    )

    if "jellyfin_api_key" in normalized and not isinstance(
        normalized["jellyfin_api_key"], SecretStr
    ):
        normalized["jellyfin_api_key"] = SecretStr(str(normalized["jellyfin_api_key"] or ""))

    model = ConfigModel.model_validate(normalized)
    if not model.outbound_allowlist_domains and model.allowlist_domains:
        if warn:
            warnings.warn(
                "allowlist_domains is deprecated; migrated into outbound_allowlist_domains",
                stacklevel=2,
            )
        model.outbound_allowlist_domains = list(model.allowlist_domains)
    return model


def try_validate_config_model(data: dict[str, Any], log=None) -> ConfigModel | None:
    try:
        return validate_config_model(data)
    except ValidationError as e:
        if log is not None:
            log.warning("config model validation failed: %s", e)
        return None
