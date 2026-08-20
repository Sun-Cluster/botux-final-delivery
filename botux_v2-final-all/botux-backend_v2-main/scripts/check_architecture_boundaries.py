#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

_LAYERS = {"api", "app", "domain", "db", "infra", "runtime", "config"}
_DENIED_IMPORTS: dict[str, set[str]] = {
    "api": set(),
    "app": {"api"},
    "domain": {"api", "app", "db", "infra", "runtime"},
    "db": {"api", "app", "infra"},
    "infra": {"api"},
    "runtime": set(),
    "config": set(),
}


@dataclass(frozen=True)
class Violation:
    file: str
    from_layer: str
    to_layer: str


def _posix(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _layer_for_file(path: Path) -> str | None:
    rel = path.relative_to(ROOT)
    if rel.as_posix() == "src/config.py":
        return "config"
    parts = rel.parts
    if len(parts) < 2 or parts[0] != "src":
        return None
    layer = parts[1]
    if layer in _LAYERS:
        return layer
    return None


def _load_allowlist(path: Path) -> set[tuple[str, str, str]]:
    if not path.exists():
        return set()
    raw = json.loads(path.read_text())
    exemptions = raw.get("architecture", {}).get("exemptions", [])
    rows: set[tuple[str, str, str]] = set()
    for item in exemptions:
        file = str(item.get("file", "")).strip()
        from_layer = str(item.get("from_layer", "")).strip()
        to_layer = str(item.get("to_layer", "")).strip()
        if file and from_layer and to_layer:
            rows.add((file, from_layer, to_layer))
    return rows


def _iter_import_layers(tree: ast.AST) -> set[str]:
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                base = alias.name.split(".", 1)[0]
                if base in _LAYERS:
                    targets.add(base)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # Relative imports stay inside package boundaries; skip.
                continue
            if not node.module:
                continue
            base = node.module.split(".", 1)[0]
            if base in _LAYERS:
                targets.add(base)
    return targets


def main() -> int:
    parser = argparse.ArgumentParser(description="Check layer dependency direction for src modules.")
    parser.add_argument(
        "--allowlist",
        default="docs/context/artifacts/refactor_guardrails_allowlist.json",
        help="Path to JSON allowlist policy.",
    )
    args = parser.parse_args()

    allowlist_path = (ROOT / args.allowlist).resolve()
    exemptions = _load_allowlist(allowlist_path)
    violations: list[Violation] = []

    for file_path in sorted(SRC.rglob("*.py")):
        from_layer = _layer_for_file(file_path)
        if from_layer is None:
            continue
        tree = ast.parse(file_path.read_text(), filename=str(file_path))
        imported_layers = _iter_import_layers(tree)
        denied = _DENIED_IMPORTS.get(from_layer, set())
        for to_layer in sorted(imported_layers):
            if to_layer not in denied:
                continue
            key = (_posix(file_path), from_layer, to_layer)
            if key in exemptions:
                continue
            violations.append(Violation(file=_posix(file_path), from_layer=from_layer, to_layer=to_layer))

    if violations:
        print("Architecture boundary violations detected:")
        for item in violations:
            print(f"- {item.file}: {item.from_layer} -> {item.to_layer} is not allowed")
        print(f"Total violations: {len(violations)}")
        return 1

    print("Architecture boundary checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
