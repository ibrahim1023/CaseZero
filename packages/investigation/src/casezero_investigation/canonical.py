import hashlib
import json
import math
from decimal import Decimal

from pydantic import JsonValue


def postgres_jsonb_text(value: JsonValue) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        if "\x00" in value:
            raise ValueError("JSONB cannot contain null characters")
        value.encode("utf-8")
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSONB requires finite numbers")
        number = Decimal(str(value))
        return format(abs(number) if number.is_zero() else number, "f")
    if isinstance(value, list):
        return "[" + ", ".join(postgres_jsonb_text(item) for item in value) + "]"
    keys = sorted(value, key=lambda key: (len(key.encode("utf-8")), key.encode("utf-8")))
    return "{" + ", ".join(
        f"{postgres_jsonb_text(key)}: {postgres_jsonb_text(value[key])}" for key in keys
    ) + "}"


def canonical_digest(value: JsonValue) -> str:
    return hashlib.sha256(postgres_jsonb_text(value).encode("utf-8")).hexdigest()
