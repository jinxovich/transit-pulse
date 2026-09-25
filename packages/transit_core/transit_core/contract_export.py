"""Экспорт контракта: JSON Schema и TypeScript-типы для дашборда.

Генератор TS сделан своим и маленьким: он покрывает ровно то подмножество
JSON Schema, которое выдаёт pydantic для ``transit_core.schemas`` (объекты,
литералы, кортежи, nullable, ``$ref``, дискриминированный union), и не требует
сети или npm. Тест сверяет закоммиченные файлы с результатом генерации.

Запуск::

    uv run python -m transit_core.contract_export --out contracts
"""

from __future__ import annotations

import argparse
import json
import sys
import typing
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter
from pydantic.json_schema import models_json_schema

from transit_core import schemas

REF_TEMPLATE = "#/$defs/{model}"
WS_MODELS = (
    schemas.WsSnapshot,
    schemas.WsVehiclesDelta,
    schemas.WsIncident,
    schemas.WsKpis,
    schemas.WsSystemStatus,
    schemas.WsPing,
)
ENUM_ALIASES = (
    "RiskLevel",
    "VehicleKind",
    "StreamMode",
    "CauseCode",
    "IncidentStatus",
    "IncidentOutcome",
    "ModelMode",
)
HEADER = (
    "// АВТОГЕНЕРАЦИЯ из packages/transit_core/transit_core/schemas.py — не редактировать руками.\n"
    "// Обновление: uv run python -m transit_core.contract_export --out contracts\n"
)


def build_json_schema() -> dict[str, Any]:
    """Собирает единую JSON Schema со всеми моделями контракта в ``$defs``."""
    models = [(m, "serialization") for m in (*schemas.REST_MODELS, *WS_MODELS)]
    _, top = models_json_schema(models, ref_template=REF_TEMPLATE)
    defs = dict(top["$defs"])
    ws = TypeAdapter(schemas.WsMessage).json_schema(mode="serialization", ref_template=REF_TEMPLATE)
    defs.update(ws.pop("$defs", {}))
    defs["WsMessage"] = ws
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "TransitPulseContract",
        "description": f"Контракт API/WS Transit Pulse, schema_version {schemas.SCHEMA_VERSION}",
        "$defs": dict(sorted(defs.items())),
    }


def _literal(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _enum_aliases() -> dict[frozenset, str]:
    return {frozenset(typing.get_args(getattr(schemas, a))): a for a in ENUM_ALIASES}


def _naive_time_pattern() -> str:
    return typing.get_args(schemas.NaiveTime)[1].metadata[0].pattern


def _ts_type(s: dict[str, Any]) -> str:
    """Переводит фрагмент JSON Schema в выражение типа TypeScript."""
    if "$ref" in s:
        return s["$ref"].rsplit("/", 1)[-1]
    if "const" in s:
        return _literal(s["const"])
    if "enum" in s:
        alias = _enum_aliases().get(frozenset(s["enum"]))
        return alias or " | ".join(_literal(v) for v in s["enum"])
    if s.get("pattern") == _naive_time_pattern():
        return "NaiveTime"
    for key in ("anyOf", "oneOf"):
        if key in s:
            return " | ".join(_ts_type(part) for part in s[key])
    kind = s.get("type")
    if kind == "array":
        if "prefixItems" in s:
            return "[" + ", ".join(_ts_type(p) for p in s["prefixItems"]) + "]"
        inner = _ts_type(s.get("items", {}))
        return f"({inner})[]" if "|" in inner else f"{inner}[]"
    if kind == "object":
        extra = s.get("additionalProperties")
        value = _ts_type(extra) if isinstance(extra, dict) else "unknown"
        return f"Record<string, {value}>"
    return {
        "string": "string",
        "integer": "number",
        "number": "number",
        "boolean": "boolean",
        "null": "null",
    }.get(kind, "unknown")


def _doc(text: str | None, indent: str = "") -> list[str]:
    if not text:
        return []
    text = text.replace("``", "`")
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if len(lines) == 1:
        return [f"{indent}/** {lines[0]} */"]
    return [f"{indent}/**", *(f"{indent} * {line}" for line in lines), f"{indent} */"]


def _interface(name: str, s: dict[str, Any]) -> list[str]:
    out = [*_doc(s.get("description")), f"export interface {name} {{"]
    required = set(s.get("required", []))
    for prop, ps in s.get("properties", {}).items():
        out += _doc(ps.get("description"), "  ")
        optional = "" if prop in required else "?"
        out.append(f"  {prop}{optional}: {_ts_type(ps)};")
    out.append("}")
    return out


def build_typescript(schema: dict[str, Any] | None = None) -> str:
    """Генерирует ``contract.ts`` из JSON Schema контракта."""
    schema = schema or build_json_schema()
    out = [HEADER, f'export const SCHEMA_VERSION = "{schemas.SCHEMA_VERSION}";', ""]
    out += _doc("Время датасета без часового пояса: YYYY-MM-DDTHH:MM:SS. Показывать как есть.")
    out.append("export type NaiveTime = string;")
    for alias in ENUM_ALIASES:
        values = typing.get_args(getattr(schemas, alias))
        out.append(f"export type {alias} = " + " | ".join(_literal(v) for v in values) + ";")
    out.append("")
    for name, s in schema["$defs"].items():
        if name == "WsMessage":
            out += _doc("Любое сообщение /ws/v1/stream; различается по полю type.")
            out.append(f"export type WsMessage = {_ts_type(s)};")
        elif s.get("type") == "object":
            out += _interface(name, s)
        else:
            out.append(f"export type {name} = {_ts_type(s)};")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def write_contract(out_dir: Path) -> list[Path]:
    """Пишет ``schema/contract.schema.json`` и ``ts/contract.ts`` в ``out_dir``."""
    schema = build_json_schema()
    json_path = out_dir / "schema" / "contract.schema.json"
    ts_path = out_dir / "ts" / "contract.ts"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    ts_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", "utf-8")
    ts_path.write_text(build_typescript(schema), "utf-8")
    return [json_path, ts_path]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Экспорт контракта в JSON Schema и TypeScript")
    parser.add_argument("--out", type=Path, default=Path("contracts"))
    args = parser.parse_args(argv)
    for path in write_contract(args.out):
        print(f"записан {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
