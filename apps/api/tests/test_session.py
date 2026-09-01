from typing import Protocol, cast

import pytest
from casezero_api.session import RuntimeRole, role_scoped_connection


class Renderable(Protocol):
    def as_string(self, context: object | None) -> str: ...


class FakeConnect(Protocol):
    async def __call__(
        self, database_url: str, *, autocommit: bool
    ) -> "FakeConnection": ...


class FakeConnection:
    def __init__(self, *, fail_reset: bool = False) -> None:
        self.commands: list[str] = []
        self.closed = False
        self.fail_reset = fail_reset

    async def execute(self, query: object) -> None:
        command = cast(Renderable, query).as_string(None)
        self.commands.append(command)
        if self.fail_reset and command == "RESET ROLE":
            raise RuntimeError("reset failed")

    async def close(self) -> None:
        self.closed = True


def connector(connection: FakeConnection) -> FakeConnect:
    async def connect(database_url: str, *, autocommit: bool) -> FakeConnection:
        assert database_url == "postgresql://fixture"
        assert autocommit is True
        return connection

    return connect


@pytest.mark.asyncio
async def test_role_scoped_connection_sets_resets_and_closes() -> None:
    connection = FakeConnection()
    async with role_scoped_connection(
        "postgresql://fixture", RuntimeRole.BLIND, connect=connector(connection)
    ) as scoped:
        assert scoped is connection

    assert connection.commands == ["SET ROLE \"casezero_blind\"", "RESET ROLE"]
    assert connection.closed


@pytest.mark.asyncio
async def test_role_scoped_connection_closes_when_reset_fails() -> None:
    connection = FakeConnection(fail_reset=True)
    with pytest.raises(RuntimeError, match="reset failed"):
        async with role_scoped_connection(
            "postgresql://fixture", RuntimeRole.PROCESSOR, connect=connector(connection)
        ):
            pass

    assert connection.closed


@pytest.mark.asyncio
async def test_role_scoped_connection_rejects_arbitrary_role() -> None:
    connection = FakeConnection()
    with pytest.raises(TypeError, match="RuntimeRole"):
        async with role_scoped_connection(
            "postgresql://fixture",
            cast(RuntimeRole, "postgres"),
            connect=connector(connection),
        ):
            pass

    assert connection.commands == []
    assert connection.closed is False
