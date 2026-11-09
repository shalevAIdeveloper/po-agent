"""Rule (CLAUDE.md, A4): every SQL statement in the API code is schema-qualified.

Unqualified names resolve through search_path, and `pg_temp` is searched first for
relations: a temp table named like ours would shadow it. po_api can create temp tables
(TEMP comes through PUBLIC, A4, kept on purpose), so the API must never rely on
search_path. This test parses app/**.py, rebuilds the SQL passed to sqlalchemy
`text(...)` (including f-strings built from module constants) and checks:
  - every relation after FROM / JOIN / INTO / UPDATE is written as schema.name;
  - every call to one of OUR functions (from supabase/migrations) is qualified.
"""

import ast
import re
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
APP = API_ROOT / "app"
MIGRATIONS = API_ROOT.parents[1] / "supabase" / "migrations"

RELATION = re.compile(r"\b(from|join|into|update)\s+(?!\()([A-Za-z_][\w$]*)(\s*\.)?", re.IGNORECASE)
# Words that can follow FROM/JOIN/… without being a relation.
NOT_RELATIONS = frozenset({"lateral", "only", "unnest", "generate_series", "jsonb_to_recordset"})
OUR_FUNCTION = re.compile(r"create\s+(?:or\s+replace\s+)?function\s+(\w+)\.(\w+)", re.IGNORECASE)


def our_function_names() -> set[str]:
    names: set[str] = set()
    for path in MIGRATIONS.glob("*.sql"):
        names.update(name for _schema, name in OUR_FUNCTION.findall(path.read_text("utf-8")))
    return names


def _module_strings(tree: ast.Module) -> dict[str, str]:
    """Module-level NAME = "..." constants (used to build SQL f-strings)."""
    found: dict[str, str] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            found[node.targets[0].id] = node.value.value
    return found


def sql_statements(path: Path) -> list[tuple[int, str]]:
    """(line, SQL) for every `text(...)` call in the module."""
    tree = ast.parse(path.read_text("utf-8"))
    constants = _module_strings(tree)
    statements: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "text"
            and node.args
        ):
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            statements.append((node.lineno, arg.value))
        elif isinstance(arg, ast.JoinedStr):
            parts: list[str] = []
            for value in arg.values:
                if isinstance(value, ast.Constant):
                    parts.append(str(value.value))
                elif (
                    isinstance(value, ast.FormattedValue)
                    and isinstance(value.value, ast.Name)
                    and value.value.id in constants
                ):
                    parts.append(constants[value.value.id])
                else:
                    pytest.fail(
                        f"{path.name}:{node.lineno}: SQL built from a non-constant; can't check it"
                    )
            statements.append((node.lineno, "".join(parts)))
        else:
            pytest.fail(f"{path.name}:{node.lineno}: text() with a non-literal; can't check it")
    return statements


def violations(sql: str, functions: set[str]) -> list[str]:
    found = [
        f"{keyword} {name} (no schema)"
        for keyword, name, dot in RELATION.findall(sql)
        if not dot and name.lower() not in NOT_RELATIONS
    ]
    for name in functions:
        # An unqualified call: `name(` not preceded by `schema.`.
        if re.search(rf"(?<![\w.]){re.escape(name)}\s*\(", sql):
            found.append(f"{name}() (no schema)")
    return found


def test_our_functions_are_found_in_the_migrations() -> None:
    assert {"create_organization", "admin_add_member", "is_org_member"} <= our_function_names()


def test_api_sql_is_schema_qualified() -> None:
    functions = our_function_names()
    problems: list[str] = []
    checked = 0
    for path in sorted(APP.rglob("*.py")):
        for line, sql in sql_statements(path):
            checked += 1
            problems += [
                f"{path.relative_to(API_ROOT)}:{line}: {v}" for v in violations(sql, functions)
            ]
    assert checked > 0, "no SQL found: the scan is broken"
    assert problems == []


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("select org_id from public.memberships where user_id = :u", []),
        ("select * from public.organizations o join public.memberships m on true", []),
        ("update public.organizations set name = :n", []),
        ("select public.create_organization(:n, :s)", []),
        ("select set_config('role', 'authenticated', true)", []),
        ("select * from unnest(:ids)", []),
        ("select * from memberships", ["from memberships (no schema)"]),
        ("select 1 from public.a join organizations o on true", ["join organizations (no schema)"]),
        ("insert into memberships values (1)", ["into memberships (no schema)"]),
        ("update organizations set x = 1", ["update organizations (no schema)"]),
        ("select create_organization(:n, :s)", ["create_organization() (no schema)"]),
    ],
)
def test_the_checker_itself(sql: str, expected: list[str]) -> None:
    assert violations(sql, {"create_organization"}) == expected
