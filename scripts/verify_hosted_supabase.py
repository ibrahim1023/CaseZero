import asyncio
from pathlib import Path

from casezero_api.hosted_verify import (
    HostedState,
    evaluate_hosted_state,
    processor_policy_exposes_final,
)
from casezero_api.settings import HostedSettings
from casezero_ntsb.curation import load_curated_manifest
from psycopg import AsyncConnection


async def inspect() -> HostedState:
    settings = HostedSettings.from_environment()
    async with await AsyncConnection.connect(settings.database_url.get_secret_value()) as connection:
        environment = await (
            await connection.execute("select name from public.casezero_environment where singleton")
        ).fetchone()
        tables = {
            str(row[0])
            for row in await (
                await connection.execute(
                    "select table_name from information_schema.tables where table_schema = 'public'"
                )
            ).fetchall()
        }
        rls_tables = {
            str(row[0])
            for row in await (
                await connection.execute(
                    """
                    select c.relname from pg_class c
                    join pg_namespace n on n.oid = c.relnamespace
                    where n.nspname = 'public'
                      and c.relrowsecurity and c.relforcerowsecurity
                    """
                )
            ).fetchall()
        }
        buckets = {
            str(row[0]): bool(row[1])
            for row in await (
                await connection.execute(
                    "select id, public from storage.buckets where id = any(%s)",
                    (["casezero-sources", "casezero-derived"],),
                )
            ).fetchall()
        }
        policy = await (
            await connection.execute(
                """
                select qual from pg_policies
                where schemaname = 'public' and tablename = 'source_documents'
                  and policyname = 'processor_sources'
                """
            )
        ).fetchone()
        eligibility = await (
            await connection.execute(
                """
                select pg_get_functiondef(
                  to_regprocedure(
                    'public.is_blind_metadata_eligible(text,text,text,timestamptz,timestamptz,text)'
                  )
                )
                """
            )
        ).fetchone()
        processor_visible_final = processor_policy_exposes_final(
            str(policy[0]) if policy else "",
            str(eligibility[0]) if eligibility and eligibility[0] else "",
        )
        public_role_grants = int(
            (
                await (
                    await connection.execute(
                        """
                        select count(*) from information_schema.role_table_grants
                        where table_schema = 'public'
                          and grantee in ('anon', 'authenticated')
                        """
                    )
                ).fetchone()
            )[0]
        )
        cutoff_column = (
            await (
                await connection.execute(
                    """
                    select 1 from information_schema.columns
                    where table_schema = 'public' and table_name = 'cases'
                      and column_name = 'evidence_cutoff'
                    """
                )
            ).fetchone()
            is not None
        )
        manifest = load_curated_manifest(
            Path(__file__).parents[1]
            / "fixtures"
            / "real-cases"
            / "cen22fa375"
            / "manifest.json"
        )
        reference = await (
            await connection.execute(
                """
                select state, evidence_cutoff,
                       exists(
                         select 1 from public.investigation_locks
                         where case_id = cases.id
                       )
                from public.cases where ntsb_number = 'CEN22FA375'
                """
            )
        ).fetchone()
        return HostedState(
            environment=str(environment[0]) if environment else "",
            tables=tables,
            rls_tables=rls_tables,
            buckets=buckets,
            processor_visible_final=processor_visible_final,
            public_role_grants=public_role_grants,
            cutoff_column=cutoff_column,
            lock_table="investigation_locks" in tables,
            audit_table="access_audit_events" in tables,
            lock_rls="investigation_locks" in rls_tables,
            audit_rls="access_audit_events" in rls_tables,
            reference_case_state=str(reference[0]) if reference else "",
            reference_cutoff_matches=(
                reference is not None and reference[1] == manifest.blind_cutoff
            ),
            reference_locked=bool(reference[2]) if reference else True,
        )


def main() -> None:
    state = asyncio.run(inspect())
    errors = evaluate_hosted_state(state)
    if errors:
        for error in errors:
            print(f"FAIL {error}")
        raise SystemExit(1)
    print("PASS hosted environment marker")
    print("PASS expected schema and RLS")
    print("PASS private source and derived buckets")
    print("PASS processor final-finding boundary")
    print("PASS reference case cutoff and unlocked state")


if __name__ == "__main__":
    main()
