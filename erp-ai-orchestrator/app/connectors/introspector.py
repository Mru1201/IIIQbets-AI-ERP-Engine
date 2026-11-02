"""Universal Schema Introspector.

Two interchangeable backends produce the same normalised :class:`RawSchema`:

* :class:`LiveDBIntrospector`  – SQLAlchemy ``inspect(engine)`` against a
  *read-only* MySQL/MariaDB DSN (profiling runs inside ``START TRANSACTION READ ONLY``).
* :class:`SQLDumpIntrospector` – a deterministic parser for phpMyAdmin/mysqldump
  ``.sql`` files (``CREATE TABLE`` + ``ALTER TABLE ... ADD PRIMARY KEY / CONSTRAINT``
  + ``INSERT`` seed rows for categorical value profiling).

:func:`introspect` tries the live DB first and falls back to the dump when the
server is offline. :func:`build_metastore` merges the raw schema with the
tenant semantic overlay and strips credential tables / secret columns.

CLI::

    python -m app.connectors.introspector                # dump -> metastore_schema.json
    python -m app.connectors.introspector --db-url mysql+pymysql://ro:***@localhost/ledger
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from app.config import (
    DEFAULT_METASTORE_PATH,
    DEFAULT_SQL_DUMP_PATH,
    EXCLUDED_COLUMN_TYPES,
    RESTRICTED_TABLES,
    SENSITIVE_COLUMN_PATTERNS,
    get_settings,
)

logger = logging.getLogger(__name__)

CATEGORICAL_MAX_DISTINCT = 20
CATEGORICAL_NAME_RE = re.compile(
    r"(status|type|mode|role|tier|scope|period|^dc$|^group$|^unit$|^gender$|department|designation|"
    r"payment_method|sale_?type|taxsystem|bb_bc|^l_i$|category$|outcome|approval|document_type)",
    re.IGNORECASE,
)
NUMERIC_NAME_RE = re.compile(
    r"(amount|price|balance|stock|quantity|qty|mrp|cost|rate|percentage|incentive|limit|total|weight)",
    re.IGNORECASE,
)
NUMERIC_VALUE_RE = re.compile(r"^-?\d+(\.\d+)?$")
TEXT_TYPES = ("varchar", "char", "text", "longtext", "mediumtext", "tinytext")


# --------------------------------------------------------------------------- #
# Normalised raw schema model
# --------------------------------------------------------------------------- #
@dataclass
class RawColumn:
    name: str
    data_type: str  # e.g. "varchar(255)", "decimal(15,2)", "enum('Dr','Cr')"
    nullable: bool = True
    default: str | None = None
    enum_values: list[str] | None = None
    observed_values: list[str] | None = None  # low-cardinality categorical profile
    numeric_text_ratio: float | None = None  # share of non-empty values that parse as numbers

    @property
    def base_type(self) -> str:
        return self.data_type.split("(")[0].strip().lower()


@dataclass
class RawForeignKey:
    name: str
    columns: list[str]
    ref_table: str
    ref_columns: list[str]
    on_delete: str | None = None


@dataclass
class RawTable:
    name: str
    columns: "OrderedDict[str, RawColumn]" = field(default_factory=OrderedDict)
    primary_key: list[str] = field(default_factory=list)
    unique_keys: dict[str, list[str]] = field(default_factory=dict)
    indexes: dict[str, list[str]] = field(default_factory=dict)
    foreign_keys: list[RawForeignKey] = field(default_factory=list)
    row_count: int | None = None


@dataclass
class RawSchema:
    database: str
    source: str  # "live_db" | "sql_dump"
    source_ref: str
    source_sha256: str | None
    tables: "OrderedDict[str, RawTable]" = field(default_factory=OrderedDict)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def is_sensitive_column(name: str) -> bool:
    lowered = name.lower()
    return any(p in lowered for p in SENSITIVE_COLUMN_PATTERNS)


def _split_ident_list(s: str) -> list[str]:
    return [c.strip().strip("`").strip() for c in s.split(",") if c.strip()]


def _parse_enum_values(data_type: str) -> list[str] | None:
    m = re.match(r"^\s*(enum|set)\((.*)\)\s*$", data_type, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    return [v.replace("''", "'") for v in re.findall(r"'((?:[^']|'')*)'", m.group(2))]


def _profile_values(col: RawColumn, values: Iterable[Any]) -> None:
    """Populate observed categorical values + numeric-in-text ratio for a column."""
    distinct: set[str] = set()
    non_empty = 0
    numeric = 0
    overflow = False
    for v in values:
        if v is None:
            continue
        s = str(v)
        if s.strip() == "":
            continue
        non_empty += 1
        if NUMERIC_VALUE_RE.match(s.strip()):
            numeric += 1
        if not overflow:
            distinct.add(s)
            if len(distinct) > CATEGORICAL_MAX_DISTINCT:
                overflow = True
    if col.base_type in TEXT_TYPES and non_empty:
        col.numeric_text_ratio = round(numeric / non_empty, 3)
    if not overflow and distinct and (col.enum_values or CATEGORICAL_NAME_RE.search(col.name)):
        col.observed_values = sorted(distinct)


# --------------------------------------------------------------------------- #
# SQL dump backend
# --------------------------------------------------------------------------- #
class SQLDumpIntrospector:
    """Parse a MySQL/MariaDB dump without executing it."""

    _create_re = re.compile(r"CREATE TABLE (?:IF NOT EXISTS )?`(?P<name>[^`]+)`\s*\((?P<body>.*?)\n\)\s*ENGINE", re.S | re.I)
    _column_re = re.compile(
        r"^`(?P<name>[^`]+)`\s+(?P<type>(?:enum|set)\((?:[^()']|'(?:[^']|'')*')*\)|[a-z]+(?:\([^)]*\))?(?:\s+unsigned)?)(?P<rest>.*)$",
        re.I,
    )
    _alter_re = re.compile(r"ALTER TABLE `(?P<name>[^`]+)`\s*(?P<body>.*?);", re.S | re.I)
    _pk_re = re.compile(r"ADD PRIMARY KEY\s*\((?P<cols>[^)]*)\)", re.I)
    _uk_re = re.compile(r"ADD UNIQUE KEY `(?P<n>[^`]+)`\s*\((?P<cols>[^)]*)\)", re.I)
    _key_re = re.compile(r"ADD (?:KEY|INDEX) `(?P<n>[^`]+)`\s*\((?P<cols>[^)]*)\)", re.I)
    _fk_re = re.compile(
        r"(?:ADD )?CONSTRAINT `(?P<n>[^`]+)` FOREIGN KEY\s*\((?P<cols>[^)]*)\)\s*REFERENCES `(?P<rt>[^`]+)`\s*\((?P<rcols>[^)]*)\)"
        r"(?:\s+ON DELETE (?P<od>CASCADE|SET NULL|RESTRICT|NO ACTION))?",
        re.I,
    )
    _insert_re = re.compile(r"INSERT INTO `(?P<name>[^`]+)`\s*\((?P<cols>[^)]*)\)\s*VALUES", re.I)

    def __init__(self, dump_path: str | Path, database: str = "ledger") -> None:
        self.dump_path = Path(dump_path)
        self.database = database

    def introspect(self, profile: bool = True) -> RawSchema:
        raw_bytes = self.dump_path.read_bytes()
        text = raw_bytes.decode("utf-8", errors="replace").replace("\r\n", "\n")
        schema = RawSchema(
            database=self.database,
            source="sql_dump",
            source_ref=self.dump_path.name,
            source_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        )
        for m in self._create_re.finditer(text):
            schema.tables[m.group("name")] = self._parse_create(m.group("name"), m.group("body"))
        for m in self._alter_re.finditer(text):
            table = schema.tables.get(m.group("name"))
            if table is not None:
                self._apply_alter(table, m.group("body"))
        if profile:
            self._profile_inserts(text, schema)
        logger.info("Parsed %d tables from dump %s", len(schema.tables), self.dump_path)
        return schema

    # -- DDL ---------------------------------------------------------------- #
    def _parse_create(self, name: str, body: str) -> RawTable:
        table = RawTable(name=name)
        for line in body.split("\n"):
            line = line.strip().rstrip(",")
            if not line:
                continue
            upper = line.upper()
            if upper.startswith("PRIMARY KEY"):
                table.primary_key = _split_ident_list(line[line.index("(") + 1 : line.rindex(")")])
                continue
            if upper.startswith(("UNIQUE KEY", "KEY ", "INDEX ")):
                continue
            if upper.startswith("CONSTRAINT"):
                fk = self._fk_re.search(line)
                if fk:
                    table.foreign_keys.append(self._fk_from_match(fk))
                continue
            cm = self._column_re.match(line)
            if not cm:
                continue
            rest = cm.group("rest")
            dm = re.search(r"\bDEFAULT\s+('(?:[^']|'')*'|\S+)", rest, re.I)
            data_type = cm.group("type").strip()
            table.columns[cm.group("name")] = RawColumn(
                name=cm.group("name"),
                data_type=data_type,
                nullable="NOT NULL" not in rest.upper(),
                default=dm.group(1).strip("'") if dm else None,
                enum_values=_parse_enum_values(data_type),
            )
        return table

    @staticmethod
    def _fk_from_match(m: re.Match) -> RawForeignKey:
        return RawForeignKey(
            name=m.group("n"),
            columns=_split_ident_list(m.group("cols")),
            ref_table=m.group("rt"),
            ref_columns=_split_ident_list(m.group("rcols")),
            on_delete=(m.group("od") or None),
        )

    def _apply_alter(self, table: RawTable, body: str) -> None:
        if (pk := self._pk_re.search(body)) is not None:
            table.primary_key = _split_ident_list(pk.group("cols"))
        for uk in self._uk_re.finditer(body):
            table.unique_keys[uk.group("n")] = _split_ident_list(uk.group("cols"))
        for k in self._key_re.finditer(body):
            table.indexes[k.group("n")] = _split_ident_list(k.group("cols"))
        for fk in self._fk_re.finditer(body):
            table.foreign_keys.append(self._fk_from_match(fk))

    # -- Seed data profiling ----------------------------------------------- #
    def _profile_inserts(self, text: str, schema: RawSchema) -> None:
        values_by_col: dict[tuple[str, str], list[Any]] = {}
        row_counts: dict[str, int] = {}
        for m in self._insert_re.finditer(text):
            tname = m.group("name")
            cols = _split_ident_list(m.group("cols"))
            rows, _ = parse_insert_values(text, m.end())
            row_counts[tname] = row_counts.get(tname, 0) + len(rows)
            for row in rows:
                for cname, val in zip(cols, row):
                    values_by_col.setdefault((tname, cname), []).append(val)
        for tname, table in schema.tables.items():
            table.row_count = row_counts.get(tname, 0)
            for cname, col in table.columns.items():
                if is_sensitive_column(cname) or col.base_type in EXCLUDED_COLUMN_TYPES:
                    continue
                _profile_values(col, values_by_col.get((tname, cname), []))


def parse_insert_values(text: str, pos: int) -> tuple[list[list[Any]], int]:
    """Tokenise ``(v1, 'v2', NULL), (...);`` starting at ``pos``.

    Handles backslash escapes, doubled quotes, nested function calls such as
    ``current_timestamp()`` and hex literals. Returns ``(rows, end_pos)``.
    """
    rows: list[list[Any]] = []
    n = len(text)
    i = pos
    while i < n:
        ch = text[i]
        if ch == ";":
            return rows, i + 1
        if ch != "(":
            i += 1
            continue
        # parse one tuple
        i += 1
        row: list[Any] = []
        while i < n:
            while i < n and text[i] in " \t\n":
                i += 1
            if text[i] == "'":
                i += 1
                buf: list[str] = []
                while i < n:
                    c = text[i]
                    if c == "\\" and i + 1 < n:
                        nxt = text[i + 1]
                        buf.append({"n": "\n", "r": "\r", "t": "\t", "0": "\0"}.get(nxt, nxt))
                        i += 2
                        continue
                    if c == "'":
                        if i + 1 < n and text[i + 1] == "'":
                            buf.append("'")
                            i += 2
                            continue
                        i += 1
                        break
                    buf.append(c)
                    i += 1
                row.append("".join(buf))
            else:
                start = i
                depth = 0
                while i < n:
                    c = text[i]
                    if c == "(":
                        depth += 1
                    elif c == ")":
                        if depth == 0:
                            break
                        depth -= 1
                    elif c == "," and depth == 0:
                        break
                    i += 1
                token = text[start:i].strip()
                row.append(None if token.upper() == "NULL" else token)
            while i < n and text[i] in " \t\n":
                i += 1
            if i < n and text[i] == ",":
                i += 1
                continue
            if i < n and text[i] == ")":
                i += 1
                break
        rows.append(row)
    return rows, i


# --------------------------------------------------------------------------- #
# Live DB backend (SQLAlchemy)
# --------------------------------------------------------------------------- #
class LiveDBIntrospector:
    """Introspect a live MySQL/MariaDB database through a read-only connection."""

    def __init__(self, db_url: str, connect_timeout: int = 5) -> None:
        self.db_url = db_url
        self.connect_timeout = connect_timeout

    def introspect(self, profile: bool = True) -> RawSchema:
        from sqlalchemy import create_engine, inspect, text
        from sqlalchemy.engine import make_url

        url = make_url(self.db_url)
        engine = create_engine(
            url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": self.connect_timeout},
            execution_options={"isolation_level": "READ COMMITTED"},
        )
        try:
            insp = inspect(engine)  # raises OperationalError if the server is offline
            schema = RawSchema(
                database=url.database or "",
                source="live_db",
                source_ref=url.render_as_string(hide_password=True),
                source_sha256=None,
            )
            for tname in sorted(insp.get_table_names()):
                table = RawTable(name=tname)
                for c in insp.get_columns(tname):
                    ctype = c["type"]
                    enums = list(getattr(ctype, "enums", []) or []) or None
                    try:
                        type_str = ctype.compile(dialect=engine.dialect)
                    except Exception:  # pragma: no cover - exotic types
                        type_str = str(ctype)
                    table.columns[c["name"]] = RawColumn(
                        name=c["name"],
                        data_type=type_str.lower() if not enums else type_str,
                        nullable=bool(c.get("nullable", True)),
                        default=None if c.get("default") is None else str(c["default"]).strip("'"),
                        enum_values=enums,
                    )
                table.primary_key = list(insp.get_pk_constraint(tname).get("constrained_columns") or [])
                for uc in insp.get_unique_constraints(tname):
                    table.unique_keys[uc["name"]] = list(uc["column_names"])
                for ix in insp.get_indexes(tname):
                    if not ix.get("unique"):
                        table.indexes[ix["name"]] = [c for c in ix["column_names"] if c]
                for fk in insp.get_foreign_keys(tname):
                    table.foreign_keys.append(
                        RawForeignKey(
                            name=fk.get("name") or "",
                            columns=list(fk["constrained_columns"]),
                            ref_table=fk["referred_table"],
                            ref_columns=list(fk["referred_columns"]),
                            on_delete=(fk.get("options") or {}).get("ondelete"),
                        )
                    )
                schema.tables[tname] = table

            if profile:
                with engine.connect() as conn:
                    conn.execute(text("START TRANSACTION READ ONLY"))
                    try:
                        for tname, table in schema.tables.items():
                            if tname in RESTRICTED_TABLES:
                                continue
                            table.row_count = int(conn.execute(text(f"SELECT COUNT(*) FROM `{tname}`")).scalar() or 0)
                            for cname, col in table.columns.items():
                                if is_sensitive_column(cname) or col.base_type in EXCLUDED_COLUMN_TYPES:
                                    continue
                                if not (col.enum_values or CATEGORICAL_NAME_RE.search(cname) or NUMERIC_NAME_RE.search(cname)):
                                    continue
                                vals = conn.execute(
                                    text(f"SELECT DISTINCT `{cname}` FROM `{tname}` LIMIT {CATEGORICAL_MAX_DISTINCT + 1}")
                                ).scalars().all()
                                _profile_values(col, vals)
                    finally:
                        conn.execute(text("ROLLBACK"))
            return schema
        finally:
            engine.dispose()


def introspect(db_url: str | None = None, dump_path: str | Path | None = None, database: str = "ledger") -> RawSchema:
    """Live DB first (if a URL is configured), SQL dump fallback."""
    if db_url:
        try:
            return LiveDBIntrospector(db_url).introspect()
        except Exception as exc:  # OperationalError, missing driver, auth failure...
            logger.warning("Live DB introspection failed (%s); falling back to SQL dump.", exc.__class__.__name__)
    path = Path(dump_path or DEFAULT_SQL_DUMP_PATH)
    if not path.exists():
        raise FileNotFoundError(f"No reachable database and SQL dump not found at {path}")
    return SQLDumpIntrospector(path, database=database).introspect()


# --------------------------------------------------------------------------- #
# Metastore builder (raw schema + semantic overlay)
# --------------------------------------------------------------------------- #
def _type_family(data_type: str) -> str:
    base = data_type.split("(")[0].strip().lower()
    if base in {"int", "integer", "bigint", "smallint", "mediumint", "tinyint"}:
        return "integer"
    if base in {"decimal", "numeric", "float", "double"}:
        return "decimal"
    if base in TEXT_TYPES or base in {"enum", "set"}:
        return "text"
    if base in {"date", "datetime", "timestamp", "time"}:
        return "temporal"
    return base


def build_metastore(raw: RawSchema, overlay: dict[str, Any] | None = None, max_row_limit: int = 1000) -> dict[str, Any]:
    """Merge the introspected schema with the tenant semantic overlay."""
    if overlay is None:
        from app.metastore.semantic_overlay import TENANT_OVERLAY as overlay  # noqa: N811

    restricted = sorted(set(RESTRICTED_TABLES) | set(overlay.get("restricted_tables", [])))
    excluded_tables: dict[str, str] = {t: "credential store (restricted)" for t in restricted}
    excluded_tables.update(overlay.get("excluded_tables", {}))
    pii_columns: dict[str, list[str]] = overlay.get("excluded_columns", {})
    table_meta: dict[str, Any] = overlay["tables"]

    tables_out: "OrderedDict[str, Any]" = OrderedDict()
    excluded_columns: dict[str, list[str]] = {}
    caveats: list[dict[str, Any]] = []

    for tname in sorted(raw.tables):
        if tname in excluded_tables:
            continue
        rt = raw.tables[tname]
        tm = table_meta.get(tname, {})
        col_meta: dict[str, Any] = tm.get("columns", {})
        cols_out: "OrderedDict[str, Any]" = OrderedDict()
        for cname, col in rt.columns.items():
            reason = None
            if is_sensitive_column(cname):
                reason = "credential/secret"
            elif col.base_type in EXCLUDED_COLUMN_TYPES:
                reason = "binary payload"
            elif cname in pii_columns.get(tname, []):
                reason = "PII"
            if reason:
                excluded_columns.setdefault(tname, []).append(cname)
                continue
            entry: dict[str, Any] = {"type": col.data_type, "nullable": col.nullable}
            cm = col_meta.get(cname, {})
            if cm.get("description"):
                entry["description"] = cm["description"]
            if col.enum_values:
                entry["enum_values"] = col.enum_values
            observed = cm.get("known_values") or col.observed_values
            if observed and not col.enum_values:
                entry["known_values"] = sorted(set(observed) | set(cm.get("known_values", [])))
            # Numeric data stored as text -> mandatory CAST caveat.
            numeric_text = col.base_type in TEXT_TYPES and (
                cm.get("numeric_text") is True
                or (cm.get("numeric_text") is not False and NUMERIC_NAME_RE.search(cname) and (col.numeric_text_ratio or 0) >= 0.9)
            )
            if numeric_text:
                cast_expr = f"CAST({tname}.{cname} AS DECIMAL(15,2))"
                entry["caveat"] = "NUMERIC_STORED_AS_TEXT"
                entry["cast_hint"] = cast_expr
                caveats.append(
                    {
                        "table": tname,
                        "column": cname,
                        "issue": "NUMERIC_STORED_AS_TEXT",
                        "severity": "high" if cm.get("numeric_text") else "medium",
                        "guidance": f"Stored as {col.data_type}. Always use {cast_expr} in SUM/AVG/comparisons/ORDER BY.",
                    }
                )
            if cm.get("caveat_note"):
                entry["note"] = cm["caveat_note"]
            cols_out[cname] = entry

        tables_out[tname] = {
            "description": tm.get("description", ""),
            "module_scope": tm.get("module_scope", ["unscoped"]),
            "primary_key": rt.primary_key,
            "unique_keys": rt.unique_keys,
            "declared_foreign_keys": [
                {"columns": fk.columns, "ref_table": fk.ref_table, "ref_columns": fk.ref_columns, "name": fk.name}
                for fk in rt.foreign_keys
            ],
            "row_count_snapshot": rt.row_count,
            "columns": cols_out,
        }

    # --- allowed joins: declared FKs + overlay logical bridges ------------- #
    joins: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()

    def _add_join(j: dict[str, Any]) -> None:
        key = (j["left_table"], j["left_column"], j["right_table"], j["right_column"])
        if key in seen or (key[2], key[3], key[0], key[1]) in seen:
            return
        lt, rt_ = tables_out.get(j["left_table"]), tables_out.get(j["right_table"])
        if not lt or not rt_ or j["left_column"] not in lt["columns"] or j["right_column"] not in rt_["columns"]:
            raise ValueError(f"allowed_join references unknown/excluded column: {key}")
        seen.add(key)
        lf = _type_family(lt["columns"][j["left_column"]]["type"])
        rf = _type_family(rt_["columns"][j["right_column"]]["type"])
        if lf != rf:
            j.setdefault(
                "caveat",
                f"Type mismatch ({j['left_table']}.{j['left_column']}={lf}, {j['right_table']}.{j['right_column']}={rf}); "
                "MySQL coerces implicitly, prefer explicit CAST on the text side.",
            )
        j["on"] = f"{j['left_table']}.{j['left_column']} = {j['right_table']}.{j['right_column']}"
        joins.append(j)

    for tname, rt in sorted(raw.tables.items()):
        for fk in rt.foreign_keys:
            if tname in excluded_tables or fk.ref_table in excluded_tables:
                continue
            for lc, rc in zip(fk.columns, fk.ref_columns):
                _add_join(
                    {
                        "id": f"{tname}.{lc}->{fk.ref_table}.{rc}",
                        "left_table": tname,
                        "left_column": lc,
                        "right_table": fk.ref_table,
                        "right_column": rc,
                        "relationship": "declared_fk",
                        "cardinality": "many_to_one",
                        "description": f"Declared FK {fk.name}",
                    }
                )
    for j in overlay.get("logical_joins", []):
        jj = dict(j)
        jj.setdefault("id", f"{jj['left_table']}.{jj['left_column']}->{jj['right_table']}.{jj['right_column']}")
        jj.setdefault("relationship", "logical")
        _add_join(jj)

    for c in overlay.get("extra_caveats", []):
        caveats.append(c)

    metrics = overlay["calculated_metrics"]
    for mname, m in metrics.items():
        for t in m["required_tables"]:
            if t not in tables_out:
                raise ValueError(f"metric {mname} requires unknown table {t}")

    return {
        "metastore_version": "1.0",
        "tenant_id": overlay["tenant_id"],
        "tenant_name": overlay["tenant_name"],
        "sql_dialect": overlay.get("sql_dialect", "mysql"),
        "database": raw.database,
        "generated_from": {"source": raw.source, "ref": raw.source_ref, "sha256": raw.source_sha256},
        "security": {
            "read_only": True,
            "max_row_limit": max_row_limit,
            "restricted_tables": restricted,
            "excluded_tables": dict(sorted(excluded_tables.items())),
            "excluded_columns": {k: sorted(v) for k, v in sorted(excluded_columns.items())},
        },
        "modules": overlay["modules"],
        "tables": tables_out,
        "allowed_joins": joins,
        "data_caveats": caveats,
        "business_rules": overlay.get("business_rules", []),
        "calculated_metrics": metrics,
    }


def write_metastore(metastore: dict[str, Any], path: str | Path = DEFAULT_METASTORE_PATH) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(metastore, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    ap = argparse.ArgumentParser(description="Introspect the tenant ERP schema into metastore_schema.json")
    ap.add_argument("--db-url", default=settings.db_url, help="Read-only SQLAlchemy URL (optional)")
    ap.add_argument("--dump", default=str(settings.sql_dump_path), help="Fallback .sql dump path")
    ap.add_argument("--out", default=str(settings.metastore_path))
    ap.add_argument("--raw", action="store_true", help="Print raw categorical profile and exit")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    raw = introspect(args.db_url, args.dump, database=settings.database_name)
    if args.raw:
        for t in raw.tables.values():
            print(f"{t.name} rows={t.row_count} pk={t.primary_key} fks={[(f.columns, f.ref_table) for f in t.foreign_keys]}")
            for c in t.columns.values():
                if c.observed_values or c.enum_values or (c.numeric_text_ratio or 0) >= 0.9:
                    print(f"   {c.name:28s} {c.data_type:22s} enum={c.enum_values} obs={c.observed_values} num%={c.numeric_text_ratio}")
        return 0
    ms = build_metastore(raw, max_row_limit=settings.max_row_limit)
    out = write_metastore(ms, args.out)
    logger.info("Wrote metastore (%d tables, %d joins, %d metrics) -> %s", len(ms["tables"]), len(ms["allowed_joins"]), len(ms["calculated_metrics"]), out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
