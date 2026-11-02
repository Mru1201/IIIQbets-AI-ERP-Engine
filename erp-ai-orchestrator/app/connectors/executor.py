"""Dual-Mode Database Execution Engine (Live MySQL/MariaDB + SQLite Auto-Fallback)."""

from __future__ import annotations

import logging
import math
import re
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote_plus

import sqlglot
from sqlglot import exp
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import (
    DataError,
    OperationalError,
    ProgrammingError,
    SQLAlchemyError,
    TimeoutError as SATimeoutError,
)

from app.config import DEFAULT_SQL_DUMP_PATH, Settings, build_mysql_url, get_settings
from app.connectors.introspector import (
    SQLDumpIntrospector,
    _split_ident_list,
    parse_insert_values,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Models & Domain Exceptions
# --------------------------------------------------------------------------- #
@dataclass
class ExecutionResult:
    """Standardized envelope for database query execution results."""

    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    execution_time_ms: float = 0.0
    row_count: int = 0
    dialect: str = "mysql"
    source_mode: str = "sqlite_fallback"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DatabaseExecutionError(Exception):
    """Base domain exception for database execution errors."""

    def __init__(
        self,
        message: str,
        error_code: str = "EXECUTION_ERROR",
        original_exception: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.original_exception = original_exception

    def __str__(self) -> str:
        if self.original_exception:
            return f"[{self.error_code}] {self.message} (Caused by: {type(self.original_exception).__name__}: {self.original_exception})"
        return f"[{self.error_code}] {self.message}"


class StatementTimeoutError(DatabaseExecutionError):
    """Raised when a query exceeds the configured execution time limit."""

    def __init__(
        self,
        message: str = "Query execution statement timeout exceeded",
        original_exception: Exception | None = None,
    ) -> None:
        super().__init__(message=message, error_code="TIMEOUT", original_exception=original_exception)


class DatabaseConnectionError(DatabaseExecutionError):
    """Raised when connecting to the database fails or the connection is dropped."""

    def __init__(
        self,
        message: str = "Database connection failed or was lost",
        original_exception: Exception | None = None,
    ) -> None:
        super().__init__(message=message, error_code="CONNECTION_ERROR", original_exception=original_exception)


class DatabaseSyntaxError(DatabaseExecutionError):
    """Raised when SQL syntax is malformed or invalid for the target dialect."""

    def __init__(
        self,
        message: str = "SQL syntax error",
        original_exception: Exception | None = None,
    ) -> None:
        super().__init__(message=message, error_code="SYNTAX_ERROR", original_exception=original_exception)


class TableNotFoundError(DatabaseExecutionError):
    """Raised when a referenced table does not exist in the schema."""

    def __init__(
        self,
        message: str = "Referenced table does not exist",
        error_code: str = "SCHEMA_ERROR",
        original_exception: Exception | None = None,
    ) -> None:
        super().__init__(message=message, error_code=error_code, original_exception=original_exception)


class ColumnNotFoundError(DatabaseExecutionError):
    """Raised when a referenced column does not exist in the schema."""

    def __init__(
        self,
        message: str = "Referenced column does not exist",
        error_code: str = "SCHEMA_ERROR",
        original_exception: Exception | None = None,
    ) -> None:
        super().__init__(message=message, error_code=error_code, original_exception=original_exception)


# --------------------------------------------------------------------------- #
# Feature 9: Result Normalization & Decimal Precision
# --------------------------------------------------------------------------- #
def normalize_value(val: Any) -> Any:
    """Normalize raw DB values: floats/decimals rounded to 2 places, dates to ISO."""
    if val is None:
        return None
    if isinstance(val, (bytes, bytearray)):
        return val.decode("utf-8", errors="replace")
    if isinstance(val, Decimal):
        return round(float(val), 2)
    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            return None
        return round(val, 2)
    if isinstance(val, datetime):
        if val.time() == dtime(0, 0, 0):
            return val.strftime("%Y-%m-%d")
        return val.isoformat()
    if isinstance(val, date):
        return val.isoformat()
    if isinstance(val, str):
        # Normalize timestamps with midnight time (e.g. "2026-10-15 00:00:00" -> "2026-10-15")
        if len(val) == 19 and val[10:] == " 00:00:00" and val[:4].isdigit():
            return val[:10]
    return val


def normalize_rows(columns: list[str], raw_rows: list[Any]) -> list[dict[str, Any]]:
    """Convert cursor rows into list of dicts with normalized values."""
    return [{col: normalize_value(val) for col, val in zip(columns, row)} for row in raw_rows]


# --------------------------------------------------------------------------- #
# Feature 8: SQLGlot Query Transpiler & SQLite UDF Registry
# --------------------------------------------------------------------------- #
def transpile_mysql_to_sqlite(sql: str) -> str:
    """Transpile a MySQL query to SQLite with date arithmetic and function mapping."""
    try:
        expression = sqlglot.parse_one(sql, read="mysql")
    except Exception as exc:
        raise DatabaseSyntaxError(f"SQL parse error: {exc}", original_exception=exc) from exc

    def _transform(node: exp.Expression) -> exp.Expression:
        # DateSub: DATE_SUB(date, INTERVAL X unit) -> DATE(date, '-X unit')
        if isinstance(node, exp.DateSub):
            inner_expr = node.expression
            unit = node.args.get("unit")
            u_str = unit.name.lower() if unit else "day"
            if isinstance(inner_expr, exp.Literal):
                val_str = str(inner_expr.this).strip()
                if val_str.startswith("-"):
                    neg_val = f"+{val_str[1:]} {u_str}"
                else:
                    neg_val = f"-{val_str} {u_str}"
                return exp.Anonymous(this="DATE", expressions=[node.this, exp.Literal.string(neg_val)])
            elif isinstance(inner_expr, exp.Column):
                neg_str = exp.Concat(expressions=[exp.Literal.string("-"), inner_expr, exp.Literal.string(f" {u_str}")])
                return exp.Anonymous(this="DATE", expressions=[node.this, neg_str])

        # NOW() -> CURRENT_TIMESTAMP
        if isinstance(node, exp.Anonymous) and node.name.upper() == "NOW":
            return exp.CurrentTimestamp()

        return node

    try:
        transformed = expression.transform(_transform)
        return transformed.sql(dialect="sqlite")
    except Exception as exc:
        raise DatabaseExecutionError(f"Transpilation error: {exc}", error_code="TRANSCRIPTION_ERROR", original_exception=exc) from exc


def register_sqlite_udfs(conn: sqlite3.Connection) -> None:
    """Register MySQL-compatible scalar functions on an active SQLite connection."""
    conn.create_function("NOW", 0, lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
    conn.create_function("CURDATE", 0, lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    conn.create_function("CURRENT_DATE", 0, lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    conn.create_function("YEAR", 1, lambda d: int(str(d)[:4]) if d and str(d)[:4].isdigit() else None)
    conn.create_function("MONTH", 1, lambda d: int(str(d)[5:7]) if d and len(str(d)) >= 7 and str(d)[5:7].isdigit() else None)
    conn.create_function("DAY", 1, lambda d: int(str(d)[8:10]) if d and len(str(d)) >= 10 and str(d)[8:10].isdigit() else None)
    conn.create_function("IFNULL", 2, lambda a, b: b if a is None else a)
    conn.create_function("CONCAT", -1, lambda *args: "".join(str(a) for a in args if a is not None))
    conn.create_function("CONCAT_WS", -1, lambda sep, *args: str(sep).join(str(a) for a in args if a is not None) if sep is not None else None)
    conn.create_function("GREATEST", -1, lambda *args: max(a for a in args if a is not None) if any(a is not None for a in args) else None)
    conn.create_function("LEAST", -1, lambda *args: min(a for a in args if a is not None) if any(a is not None for a in args) else None)
    conn.create_function("REGEXP", 2, lambda pat, s: 1 if s is not None and re.search(str(pat), str(s)) else 0)

    def _udf_regexp_like(*args: Any) -> int:
        if len(args) < 2 or args[0] is None or args[1] is None:
            return 0
        s, pat = str(args[0]), str(args[1])
        re_flags = 0
        if len(args) >= 3 and args[2] is not None:
            f_str = str(args[2]).lower()
            if "i" in f_str:
                re_flags |= re.IGNORECASE
            if "m" in f_str:
                re_flags |= re.MULTILINE
            if "s" in f_str:
                re_flags |= re.DOTALL
        try:
            return 1 if re.search(pat, s, flags=re_flags) else 0
        except Exception:
            return 0

    conn.create_function("REGEXP_LIKE", 2, lambda s, pat: 1 if s is not None and pat is not None and re.search(str(pat), str(s)) else 0)
    conn.create_function("REGEXP_LIKE", 3, _udf_regexp_like)

    def _udf_str_to_date(date_str: Any, format_str: Any) -> str | None:
        if not date_str or not format_str:
            return None
        try:
            py_fmt = str(format_str).replace("%i", "%M").replace("%s", "%S")
            dt = datetime.strptime(str(date_str), py_fmt)
            return dt.strftime("%Y-%m-%d %H:%M:%S") if "%H" in py_fmt else dt.strftime("%Y-%m-%d")
        except Exception:
            return None

    conn.create_function("STR_TO_DATE", 2, _udf_str_to_date)

    def _udf_str_to_time(*args: Any) -> str | None:
        if not args or args[0] is None:
            return None
        time_str = str(args[0])
        try:
            if len(args) >= 2 and args[1]:
                py_fmt = str(args[1]).replace("%i", "%M").replace("%s", "%S").replace("%H", "%H")
                dt = datetime.strptime(time_str, py_fmt)
                return dt.strftime("%H:%M:%S")
            if " " in time_str:
                time_str = time_str.split(" ", 1)[1]
            return time_str[:8]
        except Exception:
            return None

    conn.create_function("STR_TO_TIME", 1, _udf_str_to_time)
    conn.create_function("STR_TO_TIME", 2, _udf_str_to_time)

    def _udf_datediff(d1: Any, d2: Any) -> int | None:
        if not d1 or not d2:
            return None
        try:
            dt1 = datetime.fromisoformat(str(d1)[:10])
            dt2 = datetime.fromisoformat(str(d2)[:10])
            return (dt1 - dt2).days
        except Exception:
            return None

    conn.create_function("DATEDIFF", 2, _udf_datediff)

    def _udf_date_format(d: Any, fmt: Any) -> str | None:
        if not d or not fmt:
            return None
        try:
            s = str(d)[:19]
            dt = datetime.fromisoformat(s)
            py_fmt = str(fmt).replace("%Y", "%Y").replace("%m", "%m").replace("%d", "%d").replace("%H", "%H").replace("%i", "%M").replace("%s", "%S")
            return dt.strftime(py_fmt)
        except Exception:
            return str(d)

    conn.create_function("DATE_FORMAT", 2, _udf_date_format)

    def _udf_date_sub(d: Any, val: Any, unit: str = "day") -> str | None:
        if not d:
            return None
        try:
            dt = datetime.fromisoformat(str(d)[:10])
            v = int(val)
            u = str(unit).lower()
            if "day" in u:
                dt -= timedelta(days=v)
            elif "month" in u:
                dt -= timedelta(days=v * 30)
            elif "year" in u:
                dt -= timedelta(days=v * 365)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return str(d)

    conn.create_function("DATE_SUB", 2, _udf_date_sub)
    conn.create_function("DATE_SUB", 3, _udf_date_sub)


# --------------------------------------------------------------------------- #
# Feature 7: Automated SQLite DB Provisioning
# --------------------------------------------------------------------------- #
def mysql_to_sqlite_affinity(base_type: str) -> str:
    """Map MySQL column base types to SQLite type affinities."""
    base = base_type.lower()
    if base in ("int", "tinyint", "smallint", "mediumint", "bigint", "integer"):
        return "INTEGER"
    if base in ("decimal", "numeric"):
        return "NUMERIC"
    if base in ("float", "double", "real"):
        return "REAL"
    if base in ("blob", "longblob", "mediumblob", "tinyblob", "binary", "varbinary"):
        return "BLOB"
    return "TEXT"


class SQLiteDumpEngine:
    """Auto-provisions and queries an in-memory/file-backed SQLite database from an ERP SQL dump."""

    _insert_re = re.compile(r"INSERT INTO `(?P<name>[^`]+)`\s*\((?P<cols>[^)]*)\)\s*VALUES", re.I)

    def __init__(
        self,
        dump_path: Path | str | None = None,
        db_path: str | Path | None = None,
        timeout_s: float = 5.0,
    ) -> None:
        self.dump_path = Path(dump_path or DEFAULT_SQL_DUMP_PATH)
        self.db_path = str(db_path or "file:erp_ledger_fallback?mode=memory&cache=shared")
        self.default_timeout_s = timeout_s
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self.table_count = 0
        self.row_count = 0

    def provision(self) -> None:
        """Parse DDL and seed rows from the dump and populate the SQLite database."""
        with self._lock:
            if self._conn is not None:
                # Check if tables already exist in current connection
                cur = self._conn.cursor()
                cur.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'")
                if cur.fetchone()[0] >= 23:
                    return

            if not self.dump_path.exists():
                raise FileNotFoundError(f"SQL dump not found at {self.dump_path}")

            logger.info("Provisioning SQLite database from %s (target: %s)", self.dump_path, self.db_path)
            is_uri = self.db_path.startswith("file:")
            conn = sqlite3.connect(self.db_path, uri=is_uri, check_same_thread=False)
            register_sqlite_udfs(conn)
            cur = conn.cursor()

            # Check if database already has tables provisioned
            cur.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'")
            if cur.fetchone()[0] >= 23:
                self._conn = conn
                cur.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'")
                self.table_count = cur.fetchone()[0]
                return

            # 1. Parse table DDL definitions via SQLDumpIntrospector
            intro = SQLDumpIntrospector(self.dump_path)
            schema = intro.introspect(profile=False)

            for tname, tbl in schema.tables.items():
                col_defs: list[str] = []
                for cname, col in tbl.columns.items():
                    aff = mysql_to_sqlite_affinity(col.base_type)
                    if cname in tbl.primary_key and len(tbl.primary_key) == 1:
                        col_defs.append(f'"{cname}" {aff} PRIMARY KEY')
                    else:
                        col_defs.append(f'"{cname}" {aff}')
                if len(tbl.primary_key) > 1:
                    pk_cols = ", ".join(f'"{c}"' for c in tbl.primary_key)
                    col_defs.append(f"PRIMARY KEY ({pk_cols})")

                ddl = f'CREATE TABLE IF NOT EXISTS "{tname}" (\n  ' + ",\n  ".join(col_defs) + "\n)"
                cur.execute(ddl)

            # 2. Ingest seed rows
            text_content = self.dump_path.read_text(encoding="utf-8", errors="replace")
            total_rows = 0

            for m in self._insert_re.finditer(text_content):
                tname = m.group("name")
                cols = _split_ident_list(m.group("cols"))
                rows, _ = parse_insert_values(text_content, m.end())
                placeholders = ", ".join(["?"] * len(cols))
                col_str = ", ".join(f'"{c}"' for c in cols)
                cur.executemany(f'INSERT INTO "{tname}" ({col_str}) VALUES ({placeholders})', rows)
                total_rows += len(rows)

            conn.commit()
            self._conn = conn
            self.table_count = len(schema.tables)
            self.row_count = total_rows
            logger.info("SQLite fallback provisioned successfully: %d tables, %d rows", self.table_count, self.row_count)

    def execute_query(
        self,
        sql: str,
        tenant_id: str | int = "default",
        timeout_s: float | None = None,
        timeout_ms: int | None = None,
        **kwargs: Any,
    ) -> ExecutionResult:
        """Transpile and execute SQL against SQLite with statement timeout enforcement."""
        if isinstance(tenant_id, (int, float)) and not isinstance(tenant_id, bool):
            if timeout_ms is None and timeout_s is None:
                timeout_ms = int(tenant_id)
            tenant_id = "default"

        if timeout_s is None and timeout_ms is not None:
            timeout_s = timeout_ms / 1000.0

        effective_timeout = timeout_s if timeout_s is not None else self.default_timeout_s

        if self._conn is None:
            self.provision()

        assert self._conn is not None
        sqlite_sql = transpile_mysql_to_sqlite(sql)

        with self._lock:
            start_t = time.perf_counter()

            def _timeout_handler() -> int:
                if time.perf_counter() - start_t > effective_timeout:
                    return 1
                return 0

            self._conn.set_progress_handler(_timeout_handler, 1000)
            try:
                cur = self._conn.cursor()
                cur.execute(sqlite_sql)
                columns = [d[0] for d in cur.description] if cur.description else []
                raw_rows = cur.fetchall()
                exec_time_ms = (time.perf_counter() - start_t) * 1000.0
                norm_rows = normalize_rows(columns, raw_rows)
                return ExecutionResult(
                    columns=columns,
                    rows=norm_rows,
                    execution_time_ms=round(exec_time_ms, 2),
                    row_count=len(norm_rows),
                    dialect="sqlite",
                    source_mode="sqlite_fallback",
                )
            except sqlite3.OperationalError as exc:
                err_msg = str(exc)
                if "interrupted" in err_msg.lower():
                    raise StatementTimeoutError(
                        f"Query execution exceeded timeout limit ({effective_timeout}s)",
                        original_exception=exc,
                    ) from exc
                if "no such table" in err_msg.lower():
                    raise TableNotFoundError(f"SQLite table error: {exc}", original_exception=exc) from exc
                if "no such column" in err_msg.lower():
                    raise ColumnNotFoundError(f"SQLite column error: {exc}", original_exception=exc) from exc
                if "syntax error" in err_msg.lower() or "near " in err_msg.lower():
                    raise DatabaseSyntaxError(f"SQLite syntax error: {exc}", original_exception=exc) from exc
                raise DatabaseExecutionError(
                    f"SQLite operational error: {exc}",
                    error_code="EXECUTION_ERROR",
                    original_exception=exc,
                ) from exc
            except sqlite3.Error as exc:
                raise DatabaseExecutionError(
                    f"SQLite error: {exc}",
                    error_code="EXECUTION_ERROR",
                    original_exception=exc,
                ) from exc
            finally:
                self._conn.set_progress_handler(None, 0)

    def execute(
        self,
        sql: str,
        tenant_id: str | int = "default",
        timeout_s: float | None = None,
        timeout_ms: int | None = None,
        **kwargs: Any,
    ) -> ExecutionResult:
        """Convenience alias for execute_query."""
        return self.execute_query(
            sql=sql,
            tenant_id=tenant_id,
            timeout_s=timeout_s,
            timeout_ms=timeout_ms,
            **kwargs,
        )

    def dispose(self) -> None:
        """Close connection and reset state."""
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None


SQLiteFallbackEngine = SQLiteDumpEngine


# --------------------------------------------------------------------------- #
# Feature 6: Live MySQL Connection Pool & Dialect Error Classifier
# --------------------------------------------------------------------------- #
_CONNECTION_DROP_PATTERNS = (
    "can't connect",
    "cannot connect",
    "connection refused",
    "server has gone away",
    "lost connection",
    "broken pipe",
    "connection reset",
    "communication link failure",
    "connection closed",
    "connection is closed",
    "connection aborted",
    "connection lost",
    "remote end closed",
    "disconnected",
)


def classify_db_error(exc: Exception) -> tuple[str, str]:
    """Map DBAPI/SQLAlchemy errors to standard error codes and human-readable messages."""
    err_str = str(exc).lower()
    orig = getattr(exc, "orig", None)
    dbapi_code = None
    if orig and hasattr(orig, "args") and len(orig.args) > 0 and isinstance(orig.args[0], int):
        dbapi_code = orig.args[0]
    elif hasattr(exc, "args") and len(exc.args) > 0 and isinstance(exc.args[0], int):
        dbapi_code = exc.args[0]

    # Statement Timeout (MySQL 3024 / MariaDB 1969 / 1317)
    if (
        dbapi_code in (3024, 1969, 1317)
        or "max_statement_time exceeded" in err_str
        or "max_execution_time exceeded" in err_str
        or "statement timeout" in err_str
        or ("interrupted" in err_str and "connection" not in err_str)
    ):
        return "TIMEOUT", "Query execution exceeded statement timeout"

    # Connection failure
    if isinstance(exc, SATimeoutError) or ("pool" in err_str and "timeout" in err_str):
        return "CONNECTION_ERROR", "Connection pool checkout timeout"
    if (
        isinstance(exc, ConnectionError)
        or (orig is not None and isinstance(orig, ConnectionError))
        or dbapi_code in (2002, 2003, 2005, 2006, 2013, 2014, 2026, 2055)
        or any(pat in err_str for pat in _CONNECTION_DROP_PATTERNS)
    ):
        return "CONNECTION_ERROR", "Database server connection lost or refused"
    if dbapi_code == 1045 or "access denied" in err_str:
        return "CONNECTION_ERROR", "Database authentication failed"

    # Syntax & Schema Objects
    if isinstance(exc, ProgrammingError) or "syntax error" in err_str or dbapi_code == 1064 or "near " in err_str:
        if dbapi_code == 1146 or "no such table" in err_str or "doesn't exist" in err_str:
            return "SCHEMA_ERROR", "Referenced table does not exist"
        if dbapi_code == 1054 or "no such column" in err_str or "unknown column" in err_str:
            return "SCHEMA_ERROR", "Referenced column does not exist"
        return "SYNTAX_ERROR", "SQL syntax error"

    # Data error
    if isinstance(exc, DataError) or "division by zero" in err_str or dbapi_code == 1365:
        return "DIVISION_BY_ZERO", "Data calculation error or division by zero"

    return "EXECUTION_ERROR", str(exc)


class LiveMySQLEngine:
    """Manages SQLAlchemy QueuePool and query execution for live MySQL/MariaDB instances."""

    def __init__(
        self,
        db_url: str,
        statement_timeout_ms: int = 5000,
        pool_size: int = 5,
        max_overflow: int = 10,
        pool_timeout: float = 10.0,
        pool_recycle: int = 1800,
        connect_timeout: int = 3,
    ) -> None:
        self.db_url = db_url
        self.statement_timeout_ms = statement_timeout_ms
        self.pool_size = pool_size
        self.max_overflow = max_overflow
        self.pool_timeout = pool_timeout
        self.pool_recycle = pool_recycle
        self.connect_timeout = connect_timeout
        self._engine: Engine | None = None
        self._lock = threading.RLock()

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            with self._lock:
                if self._engine is None:
                    url = make_url(self.db_url)
                    eng = create_engine(
                        url,
                        pool_size=self.pool_size,
                        max_overflow=self.max_overflow,
                        pool_timeout=self.pool_timeout,
                        pool_recycle=self.pool_recycle,
                        pool_pre_ping=True,
                        connect_args={"connect_timeout": self.connect_timeout},
                        execution_options={"isolation_level": "READ COMMITTED"},
                    )
                    timeout_ms = self.statement_timeout_ms

                    @event.listens_for(eng, "connect")
                    def on_connect(dbapi_connection: Any, connection_record: Any) -> None:
                        cursor = dbapi_connection.cursor()
                        try:
                            # Try MySQL syntax first (milliseconds)
                            cursor.execute(f"SET SESSION max_execution_time = {timeout_ms}")
                        except Exception:
                            try:
                                # Fall back to MariaDB syntax (seconds)
                                timeout_sec = max(1, timeout_ms // 1000)
                                cursor.execute(f"SET SESSION max_statement_time = {timeout_sec}")
                            except Exception as exc:
                                logger.debug("Statement timeout configuration failed on connect: %s", exc)
                        finally:
                            cursor.close()

                    self._engine = eng
        return self._engine

    def check_liveness(self) -> bool:
        """Fast liveness probe (SELECT 1) with low socket timeout."""
        if not self.db_url:
            return False
        try:
            with self.engine.connect() as conn:
                res = conn.execute(text("SELECT 1")).scalar()
                return res == 1
        except Exception as exc:
            logger.warning("Live MySQL liveness check failed (%s: %s)", type(exc).__name__, exc)
            return False

    def execute_query(
        self,
        sql: str,
        tenant_id: str | int = "default",
        timeout_ms: int | None = None,
        timeout_s: float | None = None,
        **kwargs: Any,
    ) -> ExecutionResult:
        """Execute validated query against MySQL pool and return ExecutionResult."""
        if isinstance(tenant_id, (int, float)) and not isinstance(tenant_id, bool):
            if timeout_ms is None and timeout_s is None:
                timeout_ms = int(tenant_id)
            tenant_id = "default"

        if timeout_ms is None and timeout_s is not None:
            timeout_ms = int(timeout_s * 1000)

        start_time = time.perf_counter()
        try:
            with self.engine.connect() as conn:
                if timeout_ms is not None and timeout_ms != self.statement_timeout_ms:
                    try:
                        conn.execute(text(f"SET SESSION max_execution_time = {int(timeout_ms)}"))
                    except Exception:
                        try:
                            conn.execute(text(f"SET SESSION max_statement_time = {max(1, int(timeout_ms) // 1000)}"))
                        except Exception:
                            pass
                result = conn.execute(text(sql))
                columns = list(result.keys())
                raw_rows = result.fetchall()

                rows: list[dict[str, Any]] = []
                for raw_row in raw_rows:
                    row_dict = {}
                    for col_idx, col_name in enumerate(columns):
                        row_dict[col_name] = normalize_value(raw_row[col_idx])
                    rows.append(row_dict)

                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                return ExecutionResult(
                    columns=columns,
                    rows=rows,
                    execution_time_ms=round(elapsed_ms, 2),
                    row_count=len(rows),
                    dialect="mysql",
                    source_mode="live_mysql",
                )
        except Exception as exc:
            code, clean_msg = classify_db_error(exc)
            if code == "TIMEOUT":
                raise StatementTimeoutError(clean_msg, original_exception=exc) from exc
            if code == "CONNECTION_ERROR":
                raise DatabaseConnectionError(clean_msg, original_exception=exc) from exc
            if code == "SYNTAX_ERROR":
                raise DatabaseSyntaxError(clean_msg, original_exception=exc) from exc
            if code == "SCHEMA_ERROR":
                if "table" in clean_msg.lower():
                    raise TableNotFoundError(clean_msg, original_exception=exc) from exc
                raise ColumnNotFoundError(clean_msg, original_exception=exc) from exc
            raise DatabaseExecutionError(clean_msg, error_code=code, original_exception=exc) from exc

    def dispose(self) -> None:
        """Dispose SQLAlchemy connection pool."""
        with self._lock:
            if self._engine is not None:
                self._engine.dispose()
                self._engine = None


# --------------------------------------------------------------------------- #
# Feature 10: Unified DatabaseExecutor Facade
# --------------------------------------------------------------------------- #
class DatabaseExecutor:
    """Unified DB execution facade supporting Live MySQL with automatic SQLite fallback."""

    def __init__(
        self,
        settings: Settings | None = None,
        mode: Literal["auto", "mysql", "sqlite"] = "auto",
        db_url: str | None = None,
        sql_dump_path: Path | str | None = None,
        statement_timeout_ms: int | None = None,
        auto_fallback: bool = True,
    ) -> None:
        self.settings = settings or get_settings()
        self.requested_mode = mode
        self.explicit_db_url = db_url
        self.sqlite_dump_path = Path(sql_dump_path or self.settings.sql_dump_path)
        self.statement_timeout_ms = statement_timeout_ms or getattr(self.settings, "db_statement_timeout_ms", 5000)
        self.auto_fallback = auto_fallback
        self._mysql_engine: LiveMySQLEngine | None = None
        self._sqlite_engine: SQLiteDumpEngine | None = None
        self._resolved_mode: Literal["mysql", "sqlite"] | None = None
        self._lock = threading.RLock()

    @property
    def active_mode(self) -> str:
        """Return active engine string: 'live_mysql' or 'sqlite_fallback'."""
        mode = self.resolve_mode()
        return "live_mysql" if mode == "mysql" else "sqlite_fallback"

    @property
    def mode(self) -> str:
        """Alias for active_mode to maintain full backward compatibility."""
        return self.active_mode

    def resolve_mode(self, force_refresh: bool = False) -> Literal["mysql", "sqlite"]:
        """Resolve target database execution engine based on configuration and liveness."""
        if not force_refresh and self._resolved_mode is not None:
            return self._resolved_mode

        with self._lock:
            if not force_refresh and self._resolved_mode is not None:
                return self._resolved_mode

            if self.requested_mode == "sqlite":
                self._resolved_mode = "sqlite"
                return "sqlite"

            db_url = self._resolve_db_url()

            if self.requested_mode == "mysql":
                if not db_url:
                    raise DatabaseConnectionError("Forced MySQL mode requested, but no db_url or db_host configured.")
                eng = self._get_mysql_engine(db_url)
                if not eng.check_liveness():
                    raise DatabaseConnectionError("Forced MySQL mode requested, but server failed liveness check.")
                self._resolved_mode = "mysql"
                return "mysql"

            # Auto-detect mode
            if not db_url:
                logger.info("DatabaseExecutor [auto]: No MySQL configuration. Using SQLite fallback.")
                self._resolved_mode = "sqlite"
                return "sqlite"

            eng = self._get_mysql_engine(db_url)
            if eng.check_liveness():
                logger.info("DatabaseExecutor [auto]: Live MySQL online. Using MySQL mode.")
                self._resolved_mode = "mysql"
                return "mysql"

            logger.warning("DatabaseExecutor [auto]: Live MySQL offline. Falling back to SQLite.")
            self._resolved_mode = "sqlite"
            return "sqlite"

    def _resolve_db_url(self) -> str | None:
        """Resolve database URL from explicit parameter or settings."""
        if self.explicit_db_url:
            return self.explicit_db_url
        if self.settings.db_url:
            return self.settings.db_url
        return self.settings.build_mysql_url()

    def provision_sqlite(self) -> None:
        """Trigger explicit provisioning of the SQLite database."""
        self._get_sqlite_engine().provision()

    def execute_query(
        self,
        sql: str,
        tenant_id: str | int = "default",
        timeout_ms: int | None = None,
        timeout_s: float | None = None,
        **kwargs: Any,
    ) -> ExecutionResult:
        """Execute query on the active engine, transparently falling back to SQLite if needed."""
        # Handle polymorphic tenant_id (if int or float, caller passed timeout positionally)
        if isinstance(tenant_id, (int, float)) and not isinstance(tenant_id, bool):
            if timeout_ms is None and timeout_s is None:
                timeout_ms = int(tenant_id)
            tenant_id = "default"

        # Allow kwargs override if passed
        if "timeout_ms" in kwargs and timeout_ms is None:
            timeout_ms = kwargs.pop("timeout_ms")
        if "timeout_s" in kwargs and timeout_s is None:
            timeout_s = kwargs.pop("timeout_s")

        if timeout_ms is None and timeout_s is not None:
            timeout_ms = int(timeout_s * 1000)
        elif timeout_s is None and timeout_ms is not None:
            timeout_s = timeout_ms / 1000.0

        effective_timeout_ms = timeout_ms if timeout_ms is not None else self.statement_timeout_ms
        effective_timeout_s = timeout_s if timeout_s is not None else (effective_timeout_ms / 1000.0)

        mode = self.resolve_mode()
        if mode == "mysql":
            try:
                eng = self._get_mysql_engine()
                try:
                    return eng.execute_query(
                        sql,
                        tenant_id=str(tenant_id),
                        timeout_ms=effective_timeout_ms,
                        timeout_s=effective_timeout_s,
                        **kwargs,
                    )
                except TypeError as type_err:
                    if "unexpected keyword argument" in str(type_err):
                        return eng.execute_query(sql, tenant_id=str(tenant_id))
                    raise
            except DatabaseConnectionError as exc:
                if self.auto_fallback and self.requested_mode == "auto":
                    logger.warning("MySQL connection dropped during query (%s). Switching to SQLite fallback.", exc)
                    with self._lock:
                        self._resolved_mode = "sqlite"
                    return self._get_sqlite_engine().execute_query(
                        sql,
                        tenant_id=str(tenant_id),
                        timeout_s=effective_timeout_s,
                        timeout_ms=effective_timeout_ms,
                        **kwargs,
                    )
                raise
            except DatabaseExecutionError as exc:
                is_conn_drop = (
                    exc.error_code == "CONNECTION_ERROR"
                    or any(
                        p in str(exc).lower()
                        for p in (
                            "lost connection",
                            "broken pipe",
                            "connection reset",
                            "communication link failure",
                            "connection closed",
                            "can't connect",
                            "connection refused",
                            "server has gone away",
                        )
                    )
                )
                if is_conn_drop and self.auto_fallback and self.requested_mode == "auto":
                    logger.warning(
                        "MySQL connection dropped during query (intercepted via DatabaseExecutionError: %s). Switching to SQLite fallback.",
                        exc,
                    )
                    with self._lock:
                        self._resolved_mode = "sqlite"
                    return self._get_sqlite_engine().execute_query(
                        sql,
                        tenant_id=str(tenant_id),
                        timeout_s=effective_timeout_s,
                        timeout_ms=effective_timeout_ms,
                        **kwargs,
                    )
                raise
            except Exception as exc:
                code, clean_msg = classify_db_error(exc)
                if code == "CONNECTION_ERROR" and self.auto_fallback and self.requested_mode == "auto":
                    logger.warning(
                        "MySQL connection dropped during query (intercepted bare Exception: %s). Switching to SQLite fallback.",
                        exc,
                    )
                    with self._lock:
                        self._resolved_mode = "sqlite"
                    return self._get_sqlite_engine().execute_query(
                        sql,
                        tenant_id=str(tenant_id),
                        timeout_s=effective_timeout_s,
                        timeout_ms=effective_timeout_ms,
                        **kwargs,
                    )
                raise
        else:
            return self._get_sqlite_engine().execute_query(
                sql,
                tenant_id=str(tenant_id),
                timeout_s=effective_timeout_s,
                timeout_ms=effective_timeout_ms,
                **kwargs,
            )

    def _get_mysql_engine(self, db_url: str | None = None) -> LiveMySQLEngine:
        if self._mysql_engine is None:
            with self._lock:
                if self._mysql_engine is None:
                    url = db_url or self._resolve_db_url()
                    if not url:
                        raise DatabaseConnectionError("No MySQL database URL configured.")
                    self._mysql_engine = LiveMySQLEngine(
                        db_url=url,
                        statement_timeout_ms=self.statement_timeout_ms,
                        pool_size=getattr(self.settings, "db_pool_size", 5),
                        max_overflow=getattr(self.settings, "db_max_overflow", 10),
                        pool_timeout=getattr(self.settings, "db_pool_timeout", 10.0),
                        pool_recycle=getattr(self.settings, "db_pool_recycle", 1800),
                    )
        return self._mysql_engine

    def _get_sqlite_engine(self) -> SQLiteDumpEngine:
        if self._sqlite_engine is None:
            with self._lock:
                if self._sqlite_engine is None:
                    timeout_s = self.statement_timeout_ms / 1000.0
                    self._sqlite_engine = SQLiteDumpEngine(
                        dump_path=self.sqlite_dump_path,
                        timeout_s=timeout_s,
                    )
        return self._sqlite_engine

    def reset(self) -> None:
        """Reset internal engines and connection state."""
        with self._lock:
            if self._mysql_engine:
                self._mysql_engine.dispose()
                self._mysql_engine = None
            if self._sqlite_engine:
                self._sqlite_engine.dispose()
                self._sqlite_engine = None
            self._resolved_mode = None


__all__ = [
    "ExecutionResult",
    "DatabaseExecutionError",
    "StatementTimeoutError",
    "DatabaseConnectionError",
    "DatabaseSyntaxError",
    "TableNotFoundError",
    "ColumnNotFoundError",
    "SQLiteDumpEngine",
    "SQLiteFallbackEngine",
    "LiveMySQLEngine",
    "DatabaseExecutor",
    "classify_db_error",
    "transpile_mysql_to_sqlite",
    "normalize_value",
    "normalize_rows",
    "register_sqlite_udfs",
    "mysql_to_sqlite_affinity",
]
