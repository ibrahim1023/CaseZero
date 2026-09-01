import asyncio

from casezero_api.hosted_verify import HostedState, evaluate_hosted_state
from casezero_api.settings import HostedSettings
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
        processor_visible_final = policy is None or "INVESTIGATION_EVIDENCE" not in str(policy[0])
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


if __name__ == "__main__":
    main()
