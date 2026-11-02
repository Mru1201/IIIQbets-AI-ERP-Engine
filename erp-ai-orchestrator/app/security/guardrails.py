"""Deterministic SQLGlot AST Security Gate.

Every SQL string produced by the LLM passes through
:func:`validate_and_sanitize_sql` before it can reach a database connection.
The gate is *fail-closed*: anything it cannot positively prove safe is rejected.

Layers (in order):
 1. Pre-parse lexical checks (empty, oversize, MySQL executable comments ``/*! */``).
 2. Single-statement parse (stacked queries rejected).
 3. Root node must be ``exp.Select`` or ``exp.Union``.
 4. Full AST walk for forbidden node types (DDL/DML/DCL/session/locking/INTO).
 5. Forbidden functions & variables (SLEEP, BENCHMARK, LOAD_FILE, @@version, ...).
 6. Table whitelist (CTE-aware, cross-database and restricted tables blocked).
 7. ``SELECT *`` expansion to metastore-whitelisted columns (hides secret columns).
 8. Scope-aware column whitelist (aliases, CTEs, derived tables, correlated subqueries).
 9. Row-limit enforcement (inject / cap at ``max_rows``).
10. Comment-free re-generation in the target dialect + re-parse sanity check.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, SqlglotError
from sqlglot.optimizer.scope import Scope, traverse_scope

from app.config import RESTRICTED_TABLES

DEFAULT_MAX_ROWS = 1000
MAX_SQL_LENGTH = 20_000


class SQLSecurityViolation(Exception):
    """Raised when SQL fails any guardrail. ``code`` is a stable machine-readable reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


def _node_types(*names: str) -> tuple[type[exp.Expression], ...]:
    return tuple(t for t in (getattr(exp, n, None) for n in names) if isinstance(t, type))


# DDL / DML / DCL / session / transaction / locking / file-IO node types.
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = _node_types(
    "Drop", "Delete", "Update", "Insert", "Alter", "Create", "Command", "Grant", "Revoke", "TruncateTable",
    "Merge", "Set", "Lock", "Into", "Transaction", "Commit", "Rollback", "Use", "Show", "Pragma", "Copy",
    "LoadData", "Describe", "Kill", "Analyze", "Cache", "Uncache", "Refresh", "Comment", "Replace",
    "RenameTable", "AlterColumn", "Export", "Detach", "Attach", "Install",
)

# Session/system introspection variables and bound placeholders.
FORBIDDEN_VALUE_NODES: tuple[type[exp.Expression], ...] = _node_types(
    "SessionParameter", "Parameter", "Placeholder", "CurrentUser", "CurrentSchema", "CurrentVersion",
    "CurrentRole", "CurrentCatalog",
)

FORBIDDEN_FUNCTIONS: frozenset[str] = frozenset(
    {
        # timing / DoS
        "SLEEP", "BENCHMARK", "GET_LOCK", "RELEASE_LOCK", "RELEASE_ALL_LOCKS", "IS_FREE_LOCK", "IS_USED_LOCK",
        "MASTER_POS_WAIT", "SOURCE_POS_WAIT", "WAIT_FOR_EXECUTED_GTID_SET",
        # file / OS
        "LOAD_FILE", "SYS_EXEC", "SYS_EVAL",
        # fingerprinting
        "USER", "SYSTEM_USER", "SESSION_USER", "CURRENT_USER", "DATABASE", "SCHEMA", "VERSION",
        "CONNECTION_ID", "LAST_INSERT_ID", "ROW_COUNT", "FOUND_ROWS", "CURRENT_ROLE",
        # error-based exfiltration
        "EXTRACTVALUE", "UPDATEXML", "NAME_CONST", "GTID_SUBSET", "GTID_SUBTRACT",
        # crypto / credential helpers
        "PASSWORD", "OLD_PASSWORD", "AES_DECRYPT", "DES_DECRYPT", "DECODE", "ENCODE",
    }
)

SYSTEM_SCHEMAS: frozenset[str] = frozenset({"information_schema", "mysql", "performance_schema", "sys"})
_EXEC_COMMENT_RE = re.compile(r"/\*\s*(!|M!)", re.IGNORECASE)
_INTO_FILE_RE = re.compile(r"\bINTO\s+(OUTFILE|DUMPFILE)\b", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def validate_and_sanitize_sql(
    raw_sql: str,
    metastore: dict[str, Any],
    dialect: str = "mysql",
    max_rows: int | None = None,
) -> str:
    """Validate ``raw_sql`` against the read-only policy and tenant metastore.

    Returns the sanitized, comment-free SQL (``LIMIT`` enforced) transpiled to
    ``dialect``. Raises :class:`SQLSecurityViolation` on any policy breach.
    """
    max_rows = int(max_rows or metastore.get("security", {}).get("max_row_limit", DEFAULT_MAX_ROWS))
    policy = _Policy.from_metastore(metastore)

    # 1. Lexical pre-checks -------------------------------------------------- #
    if raw_sql is None or not str(raw_sql).strip():
        raise SQLSecurityViolation("EMPTY_QUERY", "SQL query is empty.")
    sql = str(raw_sql).strip()
    if len(sql) > MAX_SQL_LENGTH:
        raise SQLSecurityViolation("QUERY_TOO_LONG", f"SQL exceeds {MAX_SQL_LENGTH} characters.")
    if _EXEC_COMMENT_RE.search(sql):
        raise SQLSecurityViolation("EXECUTABLE_COMMENT", "MySQL executable comments (/*! ... */) are not allowed.")
    if _INTO_FILE_RE.search(sql):
        raise SQLSecurityViolation("FORBIDDEN_OPERATION", "INTO OUTFILE/DUMPFILE file operations are not allowed.")
    if "\x00" in sql:
        raise SQLSecurityViolation("PARSE_ERROR", "NUL byte in SQL.")

    # 2. Single statement ---------------------------------------------------- #
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except (ParseError, SqlglotError) as exc:
        raise SQLSecurityViolation("PARSE_ERROR", f"SQL could not be parsed: {str(exc).splitlines()[0]}") from exc
    non_empty = [s for s in statements if s is not None]
    if len(non_empty) != 1 or len(statements) - len(non_empty) > 1:
        raise SQLSecurityViolation("MULTIPLE_STATEMENTS", f"Exactly one statement allowed, got {len(non_empty)}.")
    tree = non_empty[0]

    # 3. Root node ------------------------------------------------------------ #
    if not isinstance(tree, (exp.Select, exp.Union)):
        raise SQLSecurityViolation("FORBIDDEN_STATEMENT", f"Only SELECT/UNION queries are allowed, got {type(tree).__name__}.")

    # 4/5. Full AST walk -------------------------------------------------------- #
    _check_forbidden_nodes(tree)

    # 6. Tables ---------------------------------------------------------------- #
    _check_tables(tree, policy)

    # 7. Star expansion (so secret columns can never leak through SELECT *) ------ #
    try:
        _expand_stars(tree, policy)
        # 8. Columns ------------------------------------------------------------ #
        _check_columns(tree, policy)
    except SQLSecurityViolation:
        raise
    except SqlglotError as exc:
        raise SQLSecurityViolation("UNRESOLVABLE_QUERY", f"Query scope could not be analysed: {exc}") from exc

    # 9. Row limit --------------------------------------------------------------- #
    _enforce_limit(tree, max_rows)

    # 10. Regenerate (comments stripped) + re-parse sanity ------------------------ #
    out = tree.sql(dialect=dialect, comments=False)
    reparsed = [s for s in sqlglot.parse(out, read=dialect) if s is not None]
    if len(reparsed) != 1 or not isinstance(reparsed[0], (exp.Select, exp.Union)):
        raise SQLSecurityViolation("REGENERATION_MISMATCH", "Sanitized SQL failed re-validation.")
    return out


def referenced_tables(sql: str, dialect: str = "mysql") -> list[str]:
    """Physical (non-CTE) table names referenced by ``sql`` (for plan cross-checks)."""
    tree = sqlglot.parse_one(sql, read=dialect)
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    return sorted({t.name for t in tree.find_all(exp.Table) if t.name.lower() not in ctes})


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #
class _Policy:
    def __init__(self, columns: dict[str, list[str]], restricted: set[str], database: str | None) -> None:
        self.columns_ordered = columns  # table_lower -> [original-case column names] (metastore order)
        self.columns = {t: {c.lower() for c in cols} for t, cols in columns.items()}
        self.restricted = restricted
        self.database = database.lower() if database else None

    @classmethod
    def from_metastore(cls, metastore: dict[str, Any]) -> "_Policy":
        tables = metastore.get("tables") or {}
        if not tables:
            raise SQLSecurityViolation("METASTORE_INVALID", "Metastore has no tables; refusing all queries.")
        restricted = {t.lower() for t in RESTRICTED_TABLES}
        sec = metastore.get("security", {})
        restricted |= {t.lower() for t in sec.get("restricted_tables", [])}
        restricted |= {t.lower() for t in sec.get("excluded_tables", {})}
        cols = {t.lower(): list(meta.get("columns", {}).keys()) for t, meta in tables.items() if t.lower() not in restricted}
        return cls(cols, restricted, metastore.get("database"))


def _func_name(node: exp.Expression) -> str | None:
    if isinstance(node, exp.Anonymous):
        return str(node.name).upper()
    if isinstance(node, exp.Func):
        return node.sql_name().upper()
    return None


def _check_forbidden_nodes(tree: exp.Expression) -> None:
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise SQLSecurityViolation("FORBIDDEN_OPERATION", f"Forbidden operation in AST: {type(node).__name__}.")
        if isinstance(node, FORBIDDEN_VALUE_NODES):
            raise SQLSecurityViolation("FORBIDDEN_FUNCTION", f"System/session reference not allowed: {node.sql()}.")
        name = _func_name(node)
        if name and name in FORBIDDEN_FUNCTIONS:
            raise SQLSecurityViolation("FORBIDDEN_FUNCTION", f"Function {name}() is not allowed.")


def _cte_names(tree: exp.Expression) -> set[str]:
    return {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}


def _check_tables(tree: exp.Expression, policy: _Policy) -> None:
    ctes = _cte_names(tree)
    for name in ctes:
        if name in policy.restricted:
            raise SQLSecurityViolation("RESTRICTED_TABLE", f"CTE name '{name}' shadows a restricted table.")
    found_any = False
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise SQLSecurityViolation("FORBIDDEN_OPERATION", f"Table functions are not allowed: {table.sql()}.")
        name = table.name.lower()
        db = (table.text("db") or "").lower()
        catalog = (table.text("catalog") or "").lower()
        if catalog or (db and (db in SYSTEM_SCHEMAS or (policy.database and db != policy.database))):
            raise SQLSecurityViolation("CROSS_DATABASE", f"Cross-database/system schema access blocked: {table.sql()}.")
        if name in policy.restricted:
            raise SQLSecurityViolation("RESTRICTED_TABLE", f"Access to restricted table '{table.name}' is blocked.")
        if not db and name in ctes:
            continue
        if name not in policy.columns:
            raise SQLSecurityViolation("UNKNOWN_TABLE", f"Table '{table.name}' is not in the tenant metastore.")
        found_any = True
    _ = found_any  # constant-only SELECTs (e.g. SELECT 1) are permitted


def _source_output_columns(source: Any, policy: _Policy) -> list[str] | None:
    """Ordered output column names for a scope source; ``None`` if unknown."""
    if isinstance(source, exp.Table):
        return policy.columns_ordered.get(source.name.lower())
    if isinstance(source, Scope):
        names = list(source.expression.named_selects)
        if any(isinstance(s, exp.Star) or (isinstance(s, exp.Column) and s.is_star) for s in source.expression.selects):
            return None
        return names
    return None


def _expand_stars(tree: exp.Expression, policy: _Policy) -> None:
    """Rewrite ``*`` / ``alias.*`` projections into explicit whitelisted columns (inner scopes first)."""
    for scope in traverse_scope(tree):
        select = scope.expression
        if not isinstance(select, exp.Select):
            continue
        if not any(isinstance(p, exp.Star) or (isinstance(p, exp.Column) and p.is_star) for p in select.expressions):
            continue
        new_projections: list[exp.Expression] = []
        for proj in select.expressions:
            if isinstance(proj, exp.Star):
                aliases = list(scope.selected_sources.keys())
            elif isinstance(proj, exp.Column) and proj.is_star:
                aliases = [proj.table]
            else:
                new_projections.append(proj)
                continue
            for alias in aliases:
                source = _lookup_source(scope, alias)
                if source is None:
                    raise SQLSecurityViolation("UNKNOWN_TABLE", f"Unknown table alias '{alias}' in star projection.")
                cols = _source_output_columns(source, policy)
                if cols is None:
                    raise SQLSecurityViolation("UNRESOLVABLE_QUERY", f"Cannot expand '*' for source '{alias}'.")
                for c in cols:
                    new_projections.append(
                        exp.Column(this=exp.to_identifier(c, quoted=True), table=exp.to_identifier(alias))
                    )
        select.set("expressions", new_projections)


def _lookup_source(scope: Scope, alias: str) -> Any:
    if alias in scope.sources:
        return scope.sources[alias]
    lowered = alias.lower()
    for k, v in scope.sources.items():
        if k.lower() == lowered:
            return v
    return None


def _source_has_column(source: Any, column: str, policy: _Policy) -> bool:
    cols = _source_output_columns(source, policy)
    if cols is None:
        return False
    return column.lower() in {c.lower() for c in cols}


def _iter_scope_chain(scope: Scope | None) -> Iterable[Scope]:
    while scope is not None:
        yield scope
        scope = scope.parent


def _check_columns(tree: exp.Expression, policy: _Policy) -> None:
    validated: set[int] = set()
    for scope in traverse_scope(tree):
        local_columns = [n for n in scope.walk() if isinstance(n, exp.Column)]
        for col in local_columns:
            _resolve_column(col, scope, policy)
            validated.add(id(col))
        # JOIN ... USING (col): identifiers, not Column nodes.
        if isinstance(scope.expression, exp.Select):
            for join in scope.expression.args.get("joins") or []:
                for ident in join.args.get("using") or []:
                    jt = join.this
                    src = scope.sources.get(jt.alias_or_name) if isinstance(jt, (exp.Table, exp.Subquery)) else None
                    if src is None or not _source_has_column(src, ident.name, policy):
                        raise SQLSecurityViolation("UNKNOWN_COLUMN", f"USING column '{ident.name}' not found.")
    # Fail closed: every Column node in the tree must have been resolved by some scope.
    for col in tree.find_all(exp.Column):
        if id(col) not in validated:
            raise SQLSecurityViolation("UNRESOLVABLE_QUERY", f"Column '{col.sql()}' is outside any analysable scope.")


def _resolve_column(col: exp.Column, scope: Scope, policy: _Policy) -> None:
    name = col.name
    qualifier = col.table
    if col.is_star:
        for s in _iter_scope_chain(scope):
            if _lookup_source(s, qualifier) is not None:
                return
        raise SQLSecurityViolation("UNKNOWN_TABLE", f"Unknown table alias '{qualifier}'.")

    if qualifier:
        for s in _iter_scope_chain(scope):
            src = _lookup_source(s, qualifier)
            if src is None:
                continue
            if _source_has_column(src, name, policy):
                return
            src_name = src.name if isinstance(src, exp.Table) else qualifier
            raise SQLSecurityViolation("UNKNOWN_COLUMN", f"Column '{qualifier}.{name}' does not exist on '{src_name}'.")
        raise SQLSecurityViolation("UNKNOWN_TABLE", f"Unknown table alias '{qualifier}' for column '{name}'.")

    lowered = name.lower()
    expr = scope.expression
    # Projection alias referenced from GROUP BY / HAVING / ORDER BY of the same SELECT.
    if isinstance(expr, exp.Select) and lowered in {n.lower() for n in expr.named_selects}:
        if not _inside_projection(col, expr):
            return
    # Unqualified column: current scope's FROM/JOIN sources, then outer scopes (correlated).
    for s in _iter_scope_chain(scope):
        for _node, source in s.selected_sources.values():
            if _source_has_column(source, name, policy):
                return
    raise SQLSecurityViolation("UNKNOWN_COLUMN", f"Column '{name}' does not exist in any referenced table.")


def _inside_projection(col: exp.Expression, select: exp.Select) -> bool:
    node = col
    while node.parent is not None and node.parent is not select:
        node = node.parent
    return node.parent is select and node.arg_key == "expressions"


def _enforce_limit(tree: exp.Expression, max_rows: int) -> None:
    limit = tree.args.get("limit")
    if isinstance(limit, exp.Fetch):
        raise SQLSecurityViolation("INVALID_LIMIT", "FETCH clauses are not supported; use LIMIT.")
    if limit is None:
        tree.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
        return
    value = limit.args.get("expression")
    if not (isinstance(value, exp.Literal) and not value.is_string and re.fullmatch(r"\d+", value.name or "")):
        raise SQLSecurityViolation("INVALID_LIMIT", f"LIMIT must be a non-negative integer literal, got {limit.sql()}.")
    if int(value.name) > max_rows:
        limit.set("expression", exp.Literal.number(max_rows))


class ASTGuardrail:
    """Facade class providing object-oriented and static interface for SQL validation."""

    def __init__(
        self,
        metastore: dict[str, Any] | None = None,
        dialect: str = "mysql",
        max_rows: int = 1000,
    ) -> None:
        self.metastore = metastore
        self.dialect = dialect
        self.max_rows = max_rows

    def validate_sql(
        self,
        sql: str,
        metastore: dict[str, Any] | None = None,
        max_rows: int | None = None,
    ) -> str:
        ms = metastore or self.metastore
        if ms is None:
            from app.metastore.loader import load_metastore
            ms = load_metastore()
        rows = max_rows or self.max_rows
        return validate_and_sanitize_sql(raw_sql=sql, metastore=ms, dialect=self.dialect, max_rows=rows)

    @classmethod
    def validate(
        cls,
        sql: str,
        metastore: dict[str, Any] | None = None,
        dialect: str = "mysql",
        max_rows: int = 1000,
    ) -> str:
        ms = metastore
        if ms is None:
            from app.metastore.loader import load_metastore
            ms = load_metastore()
        return validate_and_sanitize_sql(raw_sql=sql, metastore=ms, dialect=dialect, max_rows=max_rows)

