import os

import pytest
from casezero_investigation.canonical import postgres_jsonb_text
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb


def test_jsonb_text_preserves_numbers_unicode_and_byte_length_key_order() -> None:
    assert postgres_jsonb_text({"zz": [True, None, 1e-7], "é": "Δ", "a": "x"}) == (
        '{"a": "x", "zz": [true, null, 0.0000001], "é": "Δ"}'
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "\x00"])
def test_jsonb_text_rejects_unsupported_values(value) -> None:
    with pytest.raises(ValueError):
        postgres_jsonb_text(value)


@pytest.mark.db
@pytest.mark.skipif(os.getenv("CASEZERO_DB_TEST") != "1", reason="requires CASEZERO_DB_TEST=1")
async def test_replay_serialization_matches_postgres_jsonb() -> None:
    values = [
        {"zz": [True, None, 1e-7], "é": "Δ", "a": "x"},
        {"nested": {"long": 1.0, "x": -0.0}, "control": "\n\t\b\""},
        {"大": "data", "bb": 1e20, "aaa": -1.5, "model": "0.5000"},
    ]
    async with await AsyncConnection.connect(os.environ["DATABASE_URL"]) as connection:
        for value in values:
            row = await (await connection.execute("select %s::jsonb::text", (Jsonb(value),))).fetchone()
            assert row == (postgres_jsonb_text(value),)
