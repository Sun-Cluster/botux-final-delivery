#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


@dataclass(frozen=True)
class ComplexityLimit:
    max_lines: int
    max_functions: int


@dataclass(frozen=True)
class ComplexityViolation:
    file: str
    lines: int
    functions: int
    max_lines: int
    max_functions: int


def _posix(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _load_policy(path: Path) -> tuple[ComplexityLimit, dict[str, ComplexityLimit]]:
    if not path.exists():
        return ComplexityLimit(max_lines=700, max_functions=60), {}

    raw = json.loads(path.read_text())
    section = raw.get("complexity", {})
    default = ComplexityLimit(
        max_lines=int(section.get("default_max_lines", 700)),
        max_functions=int(section.get("default_max_functions", 60)),
    )

    overrides: dict[str, ComplexityLimit] = {}
    raw_overrides = section.get("overrides", {})
    if isinstance(raw_overrides, dict):
        for file, limits in raw_overrides.items():
            if not isinstance(limits, dict):
                continue
            overrides[str(file)] = ComplexityLimit(
                max_lines=int(limits.get("max_lines", default.max_lines)),
                max_functions=int(limits.get("max_functions", default.max_functions)),
            )
    return default, overrides


def _count_functions(tree: ast.AST) -> int:
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="Check module size/function complexity ceilings.")
    parser.add_argument(
        "--allowlist",
        default="docs/context/artifacts/refactor_guardrails_allowlist.json",
        help="Path to JSON allowlist policy.",
    )
    args = parser.parse_args()

    allowlist_path = (ROOT / args.allowlist).resolve()
    default_limit, overrides = _load_policy(allowlist_path)
    violations: list[ComplexityViolation] = []

    for file_path in sorted(SRC.rglob("*.py")):
        rel = _posix(file_path)
        limit = overrides.get(rel, default_limit)
        source = file_path.read_text()
        line_count = source.count("\n") + 1
        tree = ast.parse(source, filename=str(file_path))
        function_count = _count_functions(tree)

        if line_count > limit.max_lines or function_count > limit.max_functions:
            violations.append(
                ComplexityViolation(
                    file=rel,
                    lines=line_count,
                    functions=function_count,
                    max_lines=limit.max_lines,
                    max_functions=limit.max_functions,
                )
            )

    if violations:
        print("Module complexity threshold violations detected:")
        for item in violations:
            print(
                f"- {item.file}: lines={item.lines}/{item.max_lines}, "
                f"functions={item.functions}/{item.max_functions}"
            )
        print(f"Total violations: {len(violations)}")
        return 1

    print("Module complexity checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
