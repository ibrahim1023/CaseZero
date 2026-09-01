from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Protocol

from psycopg import AsyncConnection, sql


class RuntimeRole(StrEnum):
    PROCESSOR = "casezero_processor"
    BLIND = "casezero_blind"
    EVALUATION = "casezero_eval"


class ConnectionFactory(Protocol):
    async def __call__(
        self, database_url: str, *, autocommit: bool
    ) -> AsyncConnection[tuple[object, ...]]: ...


async def _connect(
    database_url: str, *, autocommit: bool
) -> AsyncConnection[tuple[object, ...]]:
    return await AsyncConnection.connect(database_url, autocommit=autocommit)


@asynccontextmanager
async def role_scoped_connection(
    database_url: str,
    role: RuntimeRole,
    *,
    connect: ConnectionFactory = _connect,
) -> AsyncIterator[AsyncConnection[tuple[object, ...]]]:
    if not isinstance(role, RuntimeRole):
        raise TypeError("role must be a RuntimeRole")
    connection = await connect(database_url, autocommit=True)
    try:
        await connection.execute(
            sql.SQL("SET ROLE {}").format(sql.Identifier(role.value))
        )
        yield connection
    finally:
        try:
            await connection.execute(sql.SQL("RESET ROLE"))
        finally:
            await connection.close()
