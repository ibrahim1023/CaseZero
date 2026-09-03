import asyncio

from casezero_api.backfill import backfill_registered_cutoffs
from casezero_api.process import load_reference_manifest
from casezero_api.repository import AcquisitionRepository
from casezero_api.settings import HostedSettings
from psycopg import AsyncConnection


async def run() -> int:
    settings = HostedSettings.from_environment()
    async with await AsyncConnection.connect(
        settings.database_url.get_secret_value()
    ) as connection:
        environment = await (
            await connection.execute(
                "select name from public.casezero_environment where singleton"
            )
        ).fetchone()
        if environment is None or environment[0] not in {"development", "staging"}:
            raise RuntimeError("cutoff backfill requires development or staging")
        manifest = load_reference_manifest("CEN22FA375")
        repository = AcquisitionRepository(connection)
        count = await backfill_registered_cutoffs(repository, (manifest,))
        case_id = await repository.get_case_id(manifest.case_id)
        state = await (
            await connection.execute(
                """
                select state, evidence_cutoff,
                       exists(select 1 from public.investigation_locks where case_id = cases.id)
                from public.cases where id = %s
                """,
                (case_id,),
            )
        ).fetchone()
        if state != ("BLIND", manifest.blind_cutoff, False):
            raise RuntimeError("cutoff backfill postcondition failed")
        return count


def main() -> None:
    count = asyncio.run(run())
    print(f"Backfilled registered case cutoffs: {count}")


if __name__ == "__main__":
    main()
