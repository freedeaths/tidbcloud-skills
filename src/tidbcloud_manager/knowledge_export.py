from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .runtime import canonical_sut_name, resolve_skill_root


_RE_LONG_NUMBER = re.compile(r"\b\d{6,}\b")
_RE_HEX = re.compile(r"\b[0-9a-fA-F]{8,}\b")
_RE_URL = re.compile(r"https?://[^\s'\"<>]+")


def _sanitize_text(value: str) -> str:
    value = _RE_URL.sub("<REDACTED_URL>", value)
    value = _RE_LONG_NUMBER.sub("<REDACTED_NUM>", value)
    value = _RE_HEX.sub("<REDACTED_HEX>", value)
    return value


def _sanitize(obj: Any) -> Any:
    if isinstance(obj, str):
        return _sanitize_text(obj)
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _pitfall_key(p: dict) -> tuple:
    trigger = p.get("trigger", {}) or {}
    error_pattern = p.get("error_pattern", {}) or {}
    return (
        trigger.get("operation_id", ""),
        trigger.get("missing_variable", ""),
        str(trigger.get("resource_state", "")),
        error_pattern.get("message_contains", ""),
    )


def _pattern_key(p: dict) -> tuple:
    trigger = p.get("trigger", {}) or {}
    return (
        p.get("name", ""),
        ",".join(trigger.get("intent_keywords", []) or []),
        str(trigger.get("precondition", "") or ""),
    )


def export_knowledge(
    *,
    sut_name: str,
    out_path: Path,
    from_dir: Path | None = None,
    min_occurrences: int = 2,
) -> dict:
    sut_name = canonical_sut_name(sut_name)
    knowledge_dir = from_dir or (Path.home() / ".tidbcloud-manager" / "knowledge" / sut_name)
    pitfalls_src = _read_yaml(knowledge_dir / "pitfalls.yaml").get("pitfalls", []) or []
    patterns_src = _read_yaml(knowledge_dir / "patterns.yaml").get("patterns", []) or []

    pitfalls = []
    for p in pitfalls_src:
        occ = int(p.get("occurrence_count", 0) or 0)
        if occ < min_occurrences:
            continue
        clean = _sanitize(p)
        # Drop timestamps that tend to be noisy in git diffs.
        clean.pop("last_occurred", None)
        pitfalls.append(clean)

    patterns = []
    for p in patterns_src:
        clean = _sanitize(p)
        clean.pop("last_used", None)
        patterns.append(clean)

    existing = _read_yaml(out_path)
    merged = dict(existing) if isinstance(existing, dict) else {}
    merged.setdefault("pitfalls", [])
    merged.setdefault("patterns", [])

    existing_pitfalls = { _pitfall_key(p): p for p in (merged.get("pitfalls", []) or []) if isinstance(p, dict) }
    existing_patterns = { _pattern_key(p): p for p in (merged.get("patterns", []) or []) if isinstance(p, dict) }

    added_pitfalls = 0
    for p in pitfalls:
        k = _pitfall_key(p)
        if k in existing_pitfalls:
            # Keep repo version as source of truth; do not overwrite silently.
            continue
        merged["pitfalls"].append(p)
        existing_pitfalls[k] = p
        added_pitfalls += 1

    added_patterns = 0
    for p in patterns:
        k = _pattern_key(p)
        if k in existing_patterns:
            continue
        merged["patterns"].append(p)
        existing_patterns[k] = p
        added_patterns += 1

    meta = merged.setdefault("export", {})
    meta["sut"] = sut_name
    meta["min_occurrences"] = min_occurrences
    meta["source_dir"] = str(knowledge_dir)
    meta["exported_at"] = datetime.now(timezone.utc).isoformat()

    _write_yaml(out_path, merged)

    return {
        "sut": sut_name,
        "source_dir": str(knowledge_dir),
        "out": str(out_path),
        "added": {"pitfalls": added_pitfalls, "patterns": added_patterns},
        "counts": {"pitfalls_exported": len(pitfalls), "patterns_exported": len(patterns)},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tidbcloud-manager knowledge")
    sub = parser.add_subparsers(dest="cmd", required=True)

    exp = sub.add_parser("export", help="Export local knowledge (~/.tidbcloud-manager/knowledge) to repo YAML")
    exp.add_argument("--sut", required=True, dest="sut_name", help="SUT name (e.g. tidbx)")
    exp.add_argument(
        "--out",
        dest="out",
        default=None,
        help="Output knowledge.yaml path (default: <skill_root>/configs/<sut>/knowledge.yaml)",
    )
    exp.add_argument(
        "--from-dir",
        dest="from_dir",
        default=None,
        help="Source knowledge directory (default: ~/.tidbcloud-manager/knowledge/<sut>)",
    )
    exp.add_argument("--min-occurrences", type=int, default=2, help="Export pitfalls with occurrence_count >= N")

    ns = parser.parse_args(argv)

    if ns.cmd == "export":
        sut_name = canonical_sut_name(ns.sut_name)
        skill_root = resolve_skill_root()
        out_path = Path(ns.out) if ns.out else (skill_root / "configs" / sut_name / "knowledge.yaml")
        if not out_path.is_absolute():
            out_path = (Path.cwd() / out_path).resolve()

        from_dir = Path(ns.from_dir).expanduser().resolve() if ns.from_dir else None
        result = export_knowledge(
            sut_name=sut_name,
            out_path=out_path,
            from_dir=from_dir,
            min_occurrences=int(ns.min_occurrences),
        )
        print(yaml.safe_dump(result, sort_keys=False, allow_unicode=True))
        return 0

    return 1
