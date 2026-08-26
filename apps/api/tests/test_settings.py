import pytest
from casezero_api.settings import HostedSettings


def values() -> dict[str, str]:
    return {
        "DATABASE_URL": "postgresql://user:pass@db.example.supabase.co:5432/postgres",
        "SUPABASE_URL": "https://project.supabase.co",
        "SUPABASE_SECRET_KEY": "secret-service-key",
        "SUPABASE_PUBLISHABLE_KEY": "publishable-key",
        "SUPABASE_JWKS_URL": "https://project.supabase.co/auth/v1/.well-known/jwks.json",
        "SUPABASE_SOURCE_BUCKET": "casezero-sources",
        "SUPABASE_DERIVED_BUCKET": "casezero-derived",
        "HYPERFUSION_API_KEY": "secret-hyperfusion-key",
        "HYPERFUSION_BASE_URL": "https://api.hyperfusion.io/v1",
        "CASEZERO_TEXT_MODEL": "qwen/qwen3-32b",
        "CASEZERO_VISION_MODEL": "google/gemma-4-31b-it",
    }


def test_hosted_settings_require_all_service_configuration() -> None:
    configured = values()
    del configured["SUPABASE_SECRET_KEY"]
    with pytest.raises(ValueError, match="SUPABASE_SECRET_KEY"):
        HostedSettings.from_mapping(configured)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"),
        ("SUPABASE_URL", "http://localhost:54321"),
    ],
)
def test_hosted_settings_reject_local_runtime_urls(key: str, value: str) -> None:
    configured = values()
    configured[key] = value
    with pytest.raises(ValueError, match="hosted"):
        HostedSettings.from_mapping(configured)


def test_settings_repr_redacts_secrets() -> None:
    settings = HostedSettings.from_mapping(values())
    rendered = repr(settings)
    assert "secret-service-key" not in rendered
    assert "secret-hyperfusion-key" not in rendered
    assert "**********" in rendered
