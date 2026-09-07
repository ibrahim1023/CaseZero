import os
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

from casezero_evidence.models import StrictModel
from casezero_investigation.models import InvestigationStage, InvestigationStatus
from casezero_investigation.repository import InvestigationRepository
from casezero_investigation.worker import Worker, runtime_configuration
from casezero_observability.reasoning import PydanticReasoningModel
from psycopg import Error
from pydantic import Field

from casezero_api.session import RuntimeRole, role_scoped_connection
from casezero_api.settings import BlindSettings


class InvestigateError(RuntimeError):
    pass


class InvestigationReport(StrictModel):
    case_id: UUID
    investigation_id: UUID
    stage: InvestigationStage
    status: InvestigationStatus
    jobs_processed: int = Field(ge=0)
    request_count: int = Field(ge=0)


def _committed_git_sha() -> str:
    root = Path(__file__).resolve().parents[4]
    paths = ("packages", "apps", "supabase/migrations", "pyproject.toml", "uv.lock")
    dirty = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", *paths], cwd=root, capture_output=True, check=False,
    )
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "--", *paths], cwd=root, capture_output=True, check=False,
    )
    if dirty.returncode or untracked.returncode or untracked.stdout:
        raise InvestigateError("UNCOMMITTED_RUNTIME")
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True)
    return result.stdout.strip()


async def investigate_from_environment(
    ntsb_number: str, *, once: bool = False, investigation_id: UUID | None = None,
) -> InvestigationReport:
    if os.getenv("CASEZERO_LIVE") != "1":
        raise InvestigateError("LIVE_OPT_IN_REQUIRED")
    try:
        settings = BlindSettings.from_mapping(os.environ)
        git_sha = _committed_git_sha()
        async with role_scoped_connection(settings.database_url.get_secret_value(), RuntimeRole.BLIND) as connection:
            row = await (await connection.execute(
                "select id from public.cases where ntsb_number=%s and state='BLIND'", (ntsb_number,),
            )).fetchone()
            if row is None or not isinstance(row[0], UUID):
                raise InvestigateError("BLIND_CASE_NOT_FOUND")
            repository = InvestigationRepository(connection)
            config = runtime_configuration(settings.text_model, git_sha)
            investigation = (
                await repository.get(investigation_id) if investigation_id is not None
                else await repository.create(row[0], config.canonical_payload())
            )
            if investigation.case_id != row[0]:
                raise InvestigateError("INVESTIGATION_CONTEXT_MISMATCH")
            count = 0
            if investigation.status not in {InvestigationStatus.SUCCEEDED, InvestigationStatus.FAILED}:
                if await repository.configuration(investigation.id) != config:
                    raise InvestigateError("RUNTIME_CONFIGURATION_MISMATCH")
                model = PydanticReasoningModel.openai_compatible(
                    settings.text_model, settings.hyperfusion_base_url,
                    settings.hyperfusion_api_key.get_secret_value(), provider="hyperfusion", single_request=True,
                )
                worker = Worker(repository, investigation, model, worker_id=str(uuid4()))
                while await worker.run_once():
                    count += 1
                    if once:
                        break
            current = await repository.get(investigation.id)
            requests = await (await connection.execute(
                "select count(*) from public.model_request_attempts where investigation_id=%s", (current.id,),
            )).fetchone()
            if requests is None or not isinstance(requests[0], int):
                raise InvestigateError("REPORT_UNAVAILABLE")
            return InvestigationReport(
                case_id=current.case_id, investigation_id=current.id, stage=current.current_stage,
                status=current.status, jobs_processed=count, request_count=requests[0],
            )
    except InvestigateError:
        raise
    except (Error, ValueError, TypeError, LookupError, RuntimeError, OSError, subprocess.SubprocessError):
        raise InvestigateError("INVESTIGATION_RUNTIME_FAILED") from None
