import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

from psycopg.conninfo import conninfo_to_dict
from pydantic import SecretStr


@dataclass(frozen=True, slots=True)
class HostedSettings:
    database_url: SecretStr
    supabase_url: str
    supabase_secret_key: SecretStr
    supabase_publishable_key: SecretStr
    supabase_jwks_url: str
    source_bucket: str
    derived_bucket: str
    hyperfusion_api_key: SecretStr
    hyperfusion_base_url: str
    text_model: str
    vision_model: str

    @classmethod
    def from_environment(cls) -> "HostedSettings":
        return cls.from_mapping(os.environ)

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "HostedSettings":
        required = {
            "DATABASE_URL": "database_url",
            "SUPABASE_URL": "supabase_url",
            "SUPABASE_SECRET_KEY": "supabase_secret_key",
            "SUPABASE_PUBLISHABLE_KEY": "supabase_publishable_key",
            "SUPABASE_JWKS_URL": "supabase_jwks_url",
            "SUPABASE_SOURCE_BUCKET": "source_bucket",
            "SUPABASE_DERIVED_BUCKET": "derived_bucket",
            "HYPERFUSION_API_KEY": "hyperfusion_api_key",
            "HYPERFUSION_BASE_URL": "hyperfusion_base_url",
            "CASEZERO_TEXT_MODEL": "text_model",
            "CASEZERO_VISION_MODEL": "vision_model",
        }
        missing = [name for name in required if not values.get(name)]
        if missing:
            raise ValueError(f"missing hosted setting: {', '.join(sorted(missing))}")
        database_url = values["DATABASE_URL"]
        supabase_url = values["SUPABASE_URL"]
        database_host_value = conninfo_to_dict(database_url).get("host")
        database_host = str(database_host_value) if database_host_value is not None else None
        if _is_local_host(database_host) or _is_local_url(supabase_url):
            raise ValueError("hosted runtime URLs must not use localhost or loopback")
        return cls(
            database_url=SecretStr(database_url),
            supabase_url=supabase_url.rstrip("/"),
            supabase_secret_key=SecretStr(values["SUPABASE_SECRET_KEY"]),
            supabase_publishable_key=SecretStr(values["SUPABASE_PUBLISHABLE_KEY"]),
            supabase_jwks_url=values["SUPABASE_JWKS_URL"],
            source_bucket=values["SUPABASE_SOURCE_BUCKET"],
            derived_bucket=values["SUPABASE_DERIVED_BUCKET"],
            hyperfusion_api_key=SecretStr(values["HYPERFUSION_API_KEY"]),
            hyperfusion_base_url=values["HYPERFUSION_BASE_URL"].rstrip("/"),
            text_model=values["CASEZERO_TEXT_MODEL"],
            vision_model=values["CASEZERO_VISION_MODEL"],
        )


def _is_local_url(url: str) -> bool:
    return _is_local_host(urlparse(url).hostname)


def _is_local_host(host: str | None) -> bool:
    return host in {"localhost", "127.0.0.1", "::1"}
