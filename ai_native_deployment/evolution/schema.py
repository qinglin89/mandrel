"""Validation against the versioned schemas under `evolution/schemas/`.

Those schema files are the contract. Restating their rules as Python
conditionals would create a second source of truth that drifts silently, so
this module reads them directly and implements exactly the JSON Schema subset
they use.

Anything outside that subset is a hard `SchemaError`, not a silent pass: when a
schema gains a keyword, it must be implemented here before any code may rely on
it being enforced. A validator that ignores what it does not understand
reports "valid" for data nobody checked.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .errors import SchemaError, ValidationError

SUPPORTED_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "$ref",
        "$defs",
        "title",
        "description",
        "type",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "const",
        "enum",
        "minLength",
        "minimum",
        "minItems",
        "pattern",
        "format",
    }
)

SUPPORTED_FORMATS = frozenset({"date-time"})

# RFC 3339 §5.6, the grammar JSON Schema's `date-time` format names. `re.ASCII`
# keeps `\d` to 0-9: Unicode decimal digits parse as a date nowhere downstream.
_RFC3339 = re.compile(
    r"\A\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})\Z",
    re.ASCII,
)

MAX_REPORTED_ERRORS = 5


def load_schema(path: Path) -> dict[str, Any]:
    """Read one schema file. Kept uncached: the files are small, and a cache
    keyed on path would serve a stale contract after an edit."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaError(f"cannot read schema: {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SchemaError(f"invalid schema JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SchemaError(f"schema must be a JSON object: {path}")
    return data


def validate(instance: Any, schema: Mapping[str, Any]) -> list[str]:
    """Return every way `instance` violates `schema`, empty when it conforms."""

    return _validate(instance, schema, root=schema, path="$")


def validate_or_raise(instance: Any, schema: Mapping[str, Any], *, description: str) -> None:
    errors = validate(instance, schema)
    if not errors:
        return
    shown = errors[:MAX_REPORTED_ERRORS]
    if len(errors) > len(shown):
        shown.append(f"... and {len(errors) - len(shown)} more")
    raise ValidationError(f"{description} does not match its schema: " + "; ".join(shown))


def _validate(instance: Any, schema: Mapping[str, Any], *, root: Mapping[str, Any], path: str) -> list[str]:
    unsupported = set(schema) - SUPPORTED_KEYWORDS
    if unsupported:
        raise SchemaError(f"unimplemented JSON Schema keywords at {path}: {sorted(unsupported)}")

    if "$ref" in schema:
        return _validate(instance, _resolve_ref(root, schema["$ref"]), root=root, path=path)

    errors: list[str] = []

    if "type" in schema:
        declared = schema["type"]
        names = declared if isinstance(declared, list) else [declared]
        if not any(_matches_type(instance, name) for name in names):
            errors.append(f"{path}: expected type {declared}, got {_type_name(instance)}")
            # Every remaining keyword assumes the declared type; reporting them
            # against the wrong shape only produces noise.
            return errors

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected {schema['const']!r}, got {instance!r}")

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} is not one of {schema['enum']!r}")

    if isinstance(instance, str):
        errors.extend(_validate_string(instance, schema, path))
    elif isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance} is below minimum {schema['minimum']}")
    elif isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: needs at least {schema['minItems']} items, got {len(instance)}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(instance):
                errors.extend(_validate(item, item_schema, root=root, path=f"{path}[{index}]"))
    elif isinstance(instance, dict):
        errors.extend(_validate_object(instance, schema, root=root, path=path))

    return errors


def _validate_string(instance: str, schema: Mapping[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    if "minLength" in schema and len(instance) < schema["minLength"]:
        errors.append(f"{path}: shorter than minLength {schema['minLength']}")
    pattern = schema.get("pattern")
    if pattern is not None and re.search(pattern, instance) is None:
        errors.append(f"{path}: {instance!r} does not match {pattern}")
    fmt = schema.get("format")
    if fmt is not None:
        if fmt not in SUPPORTED_FORMATS:
            raise SchemaError(f"unimplemented format at {path}: {fmt}")
        if not is_rfc3339_date_time(instance):
            errors.append(f"{path}: {instance!r} is not an RFC 3339 date-time")
    return errors


def _validate_object(
    instance: Mapping[str, Any],
    schema: Mapping[str, Any],
    *,
    root: Mapping[str, Any],
    path: str,
) -> list[str]:
    errors: list[str] = []
    properties = schema.get("properties") or {}

    for name in schema.get("required", []):
        if name not in instance:
            errors.append(f"{path}: missing required property {name!r}")

    if schema.get("additionalProperties") is False:
        for name in instance:
            if name not in properties:
                errors.append(f"{path}: unexpected property {name!r}")
    elif "additionalProperties" in schema:
        raise SchemaError(f"only `additionalProperties: false` is implemented (at {path})")

    for name, sub_schema in properties.items():
        if name in instance:
            errors.extend(_validate(instance[name], sub_schema, root=root, path=f"{path}.{name}"))

    return errors


def _resolve_ref(root: Mapping[str, Any], ref: Any) -> Mapping[str, Any]:
    if not isinstance(ref, str) or not ref.startswith("#/$defs/") or ref.count("/") != 2:
        raise SchemaError(f"only local '#/$defs/<name>' references are implemented, got {ref!r}")
    name = ref.rsplit("/", 1)[1]
    target = (root.get("$defs") or {}).get(name)
    if not isinstance(target, dict):
        raise SchemaError(f"unresolvable schema reference: {ref}")
    return target


def _matches_type(value: Any, name: Any) -> bool:
    # `bool` is a subclass of `int`, so the numeric cases exclude it explicitly:
    # `true` is not an acceptable `sequence`.
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "null":
        return value is None
    if name == "string":
        return isinstance(value, str)
    if name == "array":
        return isinstance(value, list)
    if name == "object":
        return isinstance(value, dict)
    raise SchemaError(f"unimplemented type: {name!r}")


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def parse_rfc3339(value: str) -> datetime:
    """One timestamp as an aware `datetime`, for age and ordering arithmetic.

    Parsing lives beside the grammar deliberately: every persisted contract in
    this package writes and reads `date-time` through this module, so there is
    one definition of what a timestamp is. A value that is not RFC 3339 raises
    rather than being coerced — an admission decision computed from a
    misread timestamp is worse than one that refuses to be computed.
    """

    if not is_rfc3339_date_time(value):
        raise ValidationError(f"not an RFC 3339 date-time: {value!r}")
    candidate = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    return datetime.fromisoformat(candidate)


def format_rfc3339(moment: datetime) -> str:
    """The wire form every persisted timestamp uses: UTC, second precision, `Z`.

    An aware datetime is required. A naive one carries no offset, so the `Z`
    this appends would be an assumption about the caller's clock rather than a
    fact about the instant.
    """

    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValidationError("timestamp must be timezone-aware; a naive datetime has no offset to record")
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def is_rfc3339_date_time(value: str) -> bool:
    """True when `value` is an RFC 3339 timestamp carrying an explicit offset.

    Grammar first, then calendar. The pattern alone would accept month 13 or a
    25-hour offset; `datetime.fromisoformat` alone accepts ISO 8601 forms
    RFC 3339 excludes — week dates, a space separator, offsets carrying seconds
    — which would let a malformed `generated_at` through the versioned schema
    and skew every later age and ordering decision.

    Leap seconds (`:60`) are refused: Python cannot represent one, so accepting
    the string would only defer the failure to whoever parses it.
    """

    if _RFC3339.match(value) is None:
        return False
    candidate = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        datetime.fromisoformat(candidate)
    except ValueError:
        return False
    return True
