import pytest
from casezero_api.cli import app
from casezero_api.investigate import InvestigateError, investigate_from_environment
from casezero_api.settings import BlindSettings
from typer.testing import CliRunner


def settings_values():
    return {
        "DATABASE_URL": "postgresql://user:password@db.example.test/casezero",
        "HYPERFUSION_API_KEY": "private-key",
        "HYPERFUSION_BASE_URL": "https://api.hyperfusion.io/v1",
        "CASEZERO_TEXT_MODEL": "qwen/qwen3-32b",
    }


async def test_live_gate_precedes_settings_connection_and_model(monkeypatch):
    monkeypatch.delenv("CASEZERO_LIVE", raising=False)
    monkeypatch.setenv("DATABASE_URL", "secret-invalid-dsn")
    with pytest.raises(InvestigateError, match="LIVE_OPT_IN_REQUIRED"):
        await investigate_from_environment("CEN22FA375")


def test_cli_refuses_without_payload(monkeypatch):
    monkeypatch.delenv("CASEZERO_LIVE", raising=False)
    result = CliRunner().invoke(app, ["investigate", "CEN22FA375"])
    assert result.exit_code == 1
    assert result.output.strip() == "Investigation failed: LIVE_OPT_IN_REQUIRED"


def test_blind_settings_do_not_require_storage_secrets():
    settings = BlindSettings.from_mapping(settings_values())
    assert settings.text_model == "qwen/qwen3-32b"
    assert "private-key" not in repr(settings)
    assert not hasattr(settings, "supabase_secret_key")


@pytest.mark.parametrize("url", [
    "http://api.hyperfusion.io/v1", "https://api.hyperfusion.io.evil.test/v1",
    "https://user:secret@api.hyperfusion.io/v1", "https://api.hyperfusion.io/v1?key=secret",
])
def test_blind_settings_reject_unapproved_endpoints_without_values(url):
    with pytest.raises(ValueError, match="INVALID_MODEL_CONFIGURATION") as error:
        BlindSettings.from_mapping(settings_values() | {"HYPERFUSION_BASE_URL": url})
    assert "secret" not in str(error.value)


async def test_uncommitted_runtime_refuses_before_connecting(monkeypatch):
    import casezero_api.investigate as runtime

    for key, value in settings_values().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("CASEZERO_LIVE", "1")

    def dirty():
        raise InvestigateError("UNCOMMITTED_RUNTIME")

    def no_connection(*args, **kwargs):
        raise AssertionError("must not connect")

    monkeypatch.setattr(runtime, "_committed_git_sha", dirty)
    monkeypatch.setattr(runtime, "role_scoped_connection", no_connection)
    with pytest.raises(InvestigateError, match="UNCOMMITTED_RUNTIME"):
        await runtime.investigate_from_environment("CEN22FA375")


def test_cli_report_is_payload_free_and_passes_resume_flags(monkeypatch):
    import json
    from uuid import uuid4

    from casezero_api import cli
    from casezero_api.investigate import InvestigationReport
    from casezero_investigation.models import InvestigationStage, InvestigationStatus

    identifier = uuid4()

    async def run(number, *, once, investigation_id):
        assert (number, once, investigation_id) == ("CEN22FA375", True, identifier)
        return InvestigationReport(case_id=uuid4(), investigation_id=identifier,
                                  stage=InvestigationStage.COMPLETE, status=InvestigationStatus.SUCCEEDED,
                                  jobs_processed=0, request_count=14)

    monkeypatch.setattr(cli, "investigate_from_environment", run)
    result = CliRunner().invoke(app, ["investigate", "CEN22FA375", "--once", "--investigation-id", str(identifier)])
    assert result.exit_code == 0
    assert set(json.loads(result.output)) == {"case_id", "investigation_id", "stage", "status", "jobs_processed", "request_count"}
