from __future__ import annotations

import ast
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = (
    REPO_ROOT / "ui",
    REPO_ROOT / "scraper",
    REPO_ROOT / "scripts",
    REPO_ROOT / "utils",
)
ROOT_MODULES = (REPO_ROOT / "api_transport.py",)
FORBIDDEN_IMPORT_ROOTS = {
    "db",
    "db_core",
    "gspread",
    "psycopg",
    "psycopg2",
    "pyodbc",
    "sqlalchemy",
}
FORBIDDEN_IMPORT_PREFIXES = {"google.oauth2", "googleapiclient"}
FORBIDDEN_STRING_FRAGMENTS = {
    "grades_neon_",
    "postgresql://",
    "applicationintent=",
    "crmsrvaddress",
    "crmsrvdb",
    "crmsrvus",
    "crmsrvps",
}


def production_python_files() -> list[Path]:
    paths = list(ROOT_MODULES)
    for root in PRODUCTION_ROOTS:
        paths.extend(root.rglob("*.py"))
    return sorted(
        path
        for path in paths
        if path.resolve() != Path(__file__).resolve()
        and "__pycache__" not in path.parts
    )


def boundary_violations(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        module_names: list[str] = []
        if isinstance(node, ast.Import):
            module_names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            module_names = [node.module]
        for module_name in module_names:
            import_root = module_name.split(".", 1)[0]
            if import_root in FORBIDDEN_IMPORT_ROOTS or any(
                module_name == prefix or module_name.startswith(prefix + ".")
                for prefix in FORBIDDEN_IMPORT_PREFIXES
            ):
                violations.append(f"line {node.lineno}: forbidden import {module_name}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            lowered = node.value.lower()
            for fragment in FORBIDDEN_STRING_FRAGMENTS:
                if fragment in lowered:
                    violations.append(
                        f"line {node.lineno}: forbidden connection-string fragment"
                    )
                    break
    return violations


def main() -> int:
    failures: list[str] = []
    for path in production_python_files():
        for violation in boundary_violations(path):
            failures.append(f"{path.relative_to(REPO_ROOT)}: {violation}")
    if failures:
        print("Direct database/Sheets boundary violations:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("Python production roots use API-only data boundaries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
