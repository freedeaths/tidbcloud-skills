from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

import yaml

from .runtime import canonical_sut_name, resolve_skill_root


def _load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f) or {}


def _spec_path_for_sut(skill_root: Path, sut_name: str) -> Path:
    sut_dir = skill_root / "configs" / sut_name
    sut_yaml = sut_dir / "sut.yaml"
    openapi_rel = "openapi.json"
    if sut_yaml.exists():
        with open(sut_yaml, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        openapi_rel = (cfg.get("specs", {}) or {}).get("openapi", openapi_rel)
    return sut_dir / openapi_rel


def load_openapi_spec(*, sut_name: str, skill_root: Path | None = None) -> tuple[Path, dict]:
    root = skill_root or resolve_skill_root()
    spec_path = _spec_path_for_sut(root, canonical_sut_name(sut_name))
    if not spec_path.exists():
        raise FileNotFoundError(f"OpenAPI spec not found: {spec_path}")
    return spec_path, _load_json(spec_path)


def iter_operations(spec: dict) -> Iterable[dict]:
    paths = spec.get("paths", {}) or {}
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, operation in methods.items():
            if not isinstance(operation, dict):
                continue
            operation_id = operation.get("operationId")
            if not operation_id:
                continue
            yield {
                "operationId": operation_id,
                "method": str(method).upper(),
                "path": path,
                "summary": operation.get("summary", "") or "",
                "tags": operation.get("tags", []) or [],
            }


def list_operations(
    spec: dict, *, query: str | None = None, limit: int | None = None
) -> list[dict]:
    q = (query or "").strip().lower()
    out: list[dict] = []
    for op in iter_operations(spec):
        if q:
            hay = f"{op.get('operationId','')} {op.get('method','')} {op.get('path','')} {op.get('summary','')}".lower()
            if q not in hay:
                continue
        out.append(op)
        if limit is not None and len(out) >= limit:
            break
    return out


def _collect_refs(obj: Any, refs: set[str]) -> None:
    if isinstance(obj, dict):
        ref = obj.get("$ref")
        if isinstance(ref, str):
            refs.add(ref)
        for v in obj.values():
            _collect_refs(v, refs)
    elif isinstance(obj, list):
        for item in obj:
            _collect_refs(item, refs)


def _schema_name_from_ref(ref: str) -> str:
    return ref.split("/")[-1]


def _extract_definitions(spec: dict, refs: set[str]) -> dict:
    defs = spec.get("definitions", {}) or {}
    extracted: dict[str, Any] = {}
    processed: set[str] = set()

    queue = set(refs)
    while queue:
        ref = queue.pop()
        if ref in processed:
            continue
        processed.add(ref)

        if not ref.startswith("#/definitions/"):
            continue

        name = _schema_name_from_ref(ref)
        schema = defs.get(name)
        if not schema:
            continue

        extracted[name] = schema
        new_refs: set[str] = set()
        _collect_refs(schema, new_refs)
        queue |= (new_refs - processed)

    # Drop noisy common types if present.
    extracted.pop("googlerpcStatus", None)
    extracted.pop("protobufAny", None)

    return extracted


def extract_operation(spec: dict, operation_id: str) -> dict:
    found_path: str | None = None
    found_method: str | None = None
    found_operation: dict | None = None

    for path, methods in (spec.get("paths", {}) or {}).items():
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            if not isinstance(op, dict):
                continue
            if op.get("operationId") == operation_id:
                found_path = path
                found_method = str(method).lower()
                found_operation = op
                break
        if found_operation:
            break

    if not found_operation or not found_path or not found_method:
        raise ValueError(f"Operation not found: {operation_id}")

    refs: set[str] = set()
    _collect_refs(found_operation, refs)
    definitions = _extract_definitions(spec, refs)

    return {
        "paths": {found_path: {found_method: found_operation}},
        "definitions": definitions,
    }


def _dump(data: Any, fmt: str) -> str:
    if fmt == "yaml":
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    return json.dumps(data, indent=2, ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tidbcloud-manager openapi")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="List operations (operationId/method/path/summary)")
    p_list.add_argument("--sut", required=True, dest="sut_name")
    p_list.add_argument("--query", default=None, help="Substring filter (operationId/method/path/summary)")
    p_list.add_argument("--limit", type=int, default=200)
    p_list.add_argument("--format", choices=["json", "yaml"], default="yaml")

    p_extract = sub.add_parser("extract", help="Extract one operation + required definitions")
    p_extract.add_argument("--sut", required=True, dest="sut_name")
    p_extract.add_argument("--operation-id", required=True)
    p_extract.add_argument("--format", choices=["json", "yaml"], default="yaml")

    ns = parser.parse_args(argv)

    try:
        _, spec = load_openapi_spec(sut_name=ns.sut_name)
        if ns.cmd == "list":
            ops = list_operations(spec, query=ns.query, limit=ns.limit)
            print(_dump({"operations": ops}, ns.format))
            return 0
        if ns.cmd == "extract":
            data = extract_operation(spec, ns.operation_id)
            print(_dump(data, ns.format))
            return 0
    except Exception as e:
        print(_dump({"success": False, "error": str(e)}, "yaml"), end="")
        return 1

    return 1
