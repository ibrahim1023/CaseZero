import asyncio
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

import httpx
import typer
from casezero_evidence.supabase_store import SupabaseSourceStore
from casezero_ntsb.carol import CarolClient
from casezero_ntsb.contextdev import ContextDevClient
from casezero_ntsb.developer_api import NtsbApiClient, NtsbApiConfigurationError
from casezero_ntsb.downloader import NtsbSourceDownloader
from casezero_ntsb.manifest import load_manifest
from psycopg import AsyncConnection

from casezero_api.ingest import IngestService, IngestSummary
from casezero_api.process import ProcessCaseError, process_from_environment
from casezero_api.repository import AcquisitionRepository
from casezero_api.settings import HostedSettings

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    pass


@app.command("process")
def process_command(ntsb_number: str) -> None:
    try:
        report = asyncio.run(process_from_environment(ntsb_number))
    except ProcessCaseError as error:
        typer.echo(f"Processing failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(report.to_json())


@app.command()
def ingest(
    ntsb_number: str,
    cutoff: Annotated[str, typer.Option(help="UTC evidence cutoff in ISO 8601 format")],
    manifest: Annotated[
        Path | None,
        typer.Option(help="Curated docket manifest; Context.dev is used when omitted"),
    ] = None,
) -> None:
    try:
        cutoff_at = _parse_utc(cutoff)
        summary = asyncio.run(ingest_from_environment(ntsb_number, manifest, cutoff_at))
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error

    typer.echo(f"Case: {summary.ntsb_number}")
    typer.echo(f"Documents fetched: {summary.documents_fetched}")
    typer.echo(f"Bytes stored: {summary.bytes_stored}")
    for visibility, count in summary.visibility_counts.items():
        typer.echo(f"{visibility}: {count}")
    typer.echo(f"Retrieval errors: {summary.retrieval_errors}")


async def ingest_from_environment(
    ntsb_number: str,
    manifest_path: Path | None,
    cutoff: datetime,
) -> IngestSummary:
    settings = HostedSettings.from_environment()
    with httpx.Client() as storage_client:
        async with httpx.AsyncClient(follow_redirects=True) as http_client:
            case_lookup = _case_lookup(http_client)
            if manifest_path is not None:
                docket_manifest = load_manifest(manifest_path)
            else:
                context_client = ContextDevClient(
                    api_key=_required_environment("CONTEXTDEV_API_KEY"),
                    http_client=http_client,
                )
                docket_manifest = await context_client.extract_manifest(
                    "https://data.ntsb.gov/Docket/?NTSBNumber=" + quote(ntsb_number, safe="")
                )

            async with await AsyncConnection.connect(
                settings.database_url.get_secret_value()
            ) as connection:
                repository = AcquisitionRepository(connection)
                downloader = NtsbSourceDownloader(
                    http_client=http_client,
                    store=SupabaseSourceStore(
                        settings.supabase_url,
                        settings.supabase_secret_key.get_secret_value(),
                        settings.source_bucket,
                        client=storage_client,
                    ),
                    repository=repository,
                )
                return await IngestService(
                    case_lookup=case_lookup,
                    downloader=downloader,
                    repository=repository,
                ).ingest(ntsb_number, docket_manifest, cutoff=cutoff)


def _case_lookup(http_client: httpx.AsyncClient) -> CarolClient | NtsbApiClient:
    key = os.getenv("NTSB_API_SUBSCRIPTION_KEY")
    endpoint = os.getenv("NTSB_GET_CASE_URL_TEMPLATE")
    if key or endpoint:
        if not key or not endpoint:
            raise NtsbApiConfigurationError(
                "NTSB_API_SUBSCRIPTION_KEY and NTSB_GET_CASE_URL_TEMPLATE must be set together"
            )
        return NtsbApiClient(
            subscription_key=key,
            endpoint_template=endpoint,
            http_client=http_client,
        )
    return CarolClient(http_client=http_client)


def _required_environment(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("cutoff must be an ISO 8601 datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("cutoff must be UTC-aware")
    return parsed
