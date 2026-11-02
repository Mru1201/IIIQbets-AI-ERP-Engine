"""Comprehensive test suite for Dual-Mode DB Execution Engine (Milestone 2)."""

from __future__ import annotations

import concurrent.futures
from datetime import date, datetime
from decimal import Decimal
import math
from pathlib import Path
import sqlite3
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy.exc
from sqlalchemy.engine import CursorResult

from app.config import DEFAULT_SQL_DUMP_PATH, Settings, build_mysql_url
from app.connectors.executor import (
    ColumnNotFoundError,
    DatabaseConnectionError,
    DatabaseExecutionError,
    DatabaseExecutor,
    DatabaseSyntaxError,
    ExecutionResult,
    LiveMySQLEngine,
    SQLiteDumpEngine,
    StatementTimeoutError,
    TableNotFoundError,
    classify_db_error,
    normalize_rows,
    normalize_value,
    transpile_mysql_to_sqlite,
)
from app.metastore.loader import load_metastore


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def dump_path() -> Path:
    return DEFAULT_SQL_DUMP_PATH


@pytest.fixture(scope="module")
def metastore() -> dict:
    return load_metastore()


@pytest.fixture
def sqlite_executor(dump_path: Path) -> DatabaseExecutor:
    """DatabaseExecutor instance running strictly in SQLite fallback mode."""
    return DatabaseExecutor(
        db_url=None,
        mode="sqlite",
        sql_dump_path=dump_path,
        auto_fallback=True,
    )


# --------------------------------------------------------------------------- #
# Class 1: TestSQLiteAutoProvisioning
# --------------------------------------------------------------------------- #
class TestSQLiteAutoProvisioning:
    def test_provision_creates_all_23_tables(self, sqlite_executor: DatabaseExecutor) -> None:
        sqlite_executor.provision_sqlite()
        engine = sqlite_executor._get_sqlite_engine()
        assert engine._conn is not None

        cur = engine._conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'")
        tables = {row[0] for row in cur.fetchall()}

        expected_tables = {
            "accountgroup", "accounts", "batches", "cart_items", "categories",
            "categories_discount_history", "chatbot_audit_log", "companies",
            "company_info", "credit_periods", "direct_deposit", "expensive",
            "gsp_api", "gsp_credentials", "offers", "order_items", "orders",
            "products", "sales_visits", "saletypes", "units", "voucher", "voucherdetails"
        }
        assert len(tables) == 23
        assert tables == expected_tables

    def test_provision_seeds_all_284_rows(self, sqlite_executor: DatabaseExecutor) -> None:
        sqlite_executor.provision_sqlite()
        engine = sqlite_executor._get_sqlite_engine()
        assert engine._conn is not None
        cur = engine._conn.cursor()

        counts = {
            "accounts": 55,
            "voucher": 5,
            "voucherdetails": 5,
            "batches": 15,
            "companies": 35,
            "expensive": 5,
            "orders": 0,
            "order_items": 0,
            "accountgroup": 34,
            "categories": 30,
            "direct_deposit": 20,
            "products": 15,
        }

        total_rows = 0
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'")
        tables = [row[0] for row in cur.fetchall()]
        for table in tables:
            cur.execute(f'SELECT COUNT(*) FROM "{table}"')
            c = cur.fetchone()[0]
            total_rows += c
            if table in counts:
                assert c == counts[table], f"Table {table} expected {counts[table]} rows, found {c}"

        assert total_rows == 284

    def test_inline_primary_keys_created(self, sqlite_executor: DatabaseExecutor) -> None:
        sqlite_executor.provision_sqlite()
        engine = sqlite_executor._get_sqlite_engine()
        assert engine._conn is not None
        cur = engine._conn.cursor()

        # accounts primary key is 'id'
        cur.execute('PRAGMA table_info("accounts")')
        cols = {row[1]: row[5] for row in cur.fetchall()}  # col[1] = name, col[5] = pk flag
        assert cols.get("id") == 1

        # voucher primary key is 'VoucherID'
        cur.execute('PRAGMA table_info("voucher")')
        cols_voucher = {row[1]: row[5] for row in cur.fetchall()}
        assert cols_voucher.get("VoucherID") == 1

        # expensive has NO primary key in dump
        cur.execute('PRAGMA table_info("expensive")')
        cols_expensive = {row[1]: row[5] for row in cur.fetchall()}
        assert all(pk_flag == 0 for pk_flag in cols_expensive.values())

    def test_mysql_syntax_modifiers_stripped(self, sqlite_executor: DatabaseExecutor) -> None:
        sqlite_executor.provision_sqlite()
        engine = sqlite_executor._get_sqlite_engine()
        assert engine._conn is not None
        cur = engine._conn.cursor()

        # batches had ON UPDATE current_timestamp()
        cur.execute('SELECT COUNT(*) FROM "batches"')
        assert cur.fetchone()[0] == 15

        # accounts had CHARACTER SET utf8mb4 COLLATE utf8mb4_bin
        cur.execute('SELECT COUNT(*) FROM "accounts"')
        assert cur.fetchone()[0] == 55

    def test_provision_idempotency(self, sqlite_executor: DatabaseExecutor) -> None:
        sqlite_executor.provision_sqlite()
        sqlite_executor.provision_sqlite()
        sqlite_executor.provision_sqlite()

        engine = sqlite_executor._get_sqlite_engine()
        assert engine._conn is not None
        cur = engine._conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'")
        tables = [row[0] for row in cur.fetchall()]
        assert len(tables) == 23

        total = sum(cur.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables)
        assert total == 284

    def test_missing_dump_file_raises_error(self, tmp_path: Path) -> None:
        non_existent = tmp_path / "does_not_exist.sql"
        executor = DatabaseExecutor(sql_dump_path=non_existent, mode="sqlite")
        with pytest.raises(FileNotFoundError):
            executor.provision_sqlite()


# --------------------------------------------------------------------------- #
# Class 2: TestSQLGlotTranspilationAndExecution
# --------------------------------------------------------------------------- #
class TestSQLGlotTranspilationAndExecution:
    DATE_RANGE = {"from_date": "2020-01-01", "to_date": "2030-12-31"}

    def test_metric_total_sales_revenue(self, sqlite_executor: DatabaseExecutor, metastore: dict) -> None:
        raw_sql = metastore["calculated_metrics"]["total_sales_revenue"]["canonical_sql"].format(**self.DATE_RANGE)
        res = sqlite_executor.execute_query(raw_sql)
        assert res.dialect == "sqlite"
        assert res.row_count == 1
        assert res.rows[0]["total_sales_revenue"] == 10368.0
        assert res.rows[0]["sales_invoice_count"] == 3

    def test_metric_cost_of_goods_sold(self, sqlite_executor: DatabaseExecutor, metastore: dict) -> None:
        raw_sql = metastore["calculated_metrics"]["cost_of_goods_sold"]["canonical_sql"].format(**self.DATE_RANGE)
        res = sqlite_executor.execute_query(raw_sql)
        assert res.row_count == 1
        assert res.rows[0]["cost_of_goods_sold"] == 7900.0
        assert res.rows[0]["total_units_sold"] == 7.0

    def test_metric_total_expenses(self, sqlite_executor: DatabaseExecutor, metastore: dict) -> None:
        raw_sql = metastore["calculated_metrics"]["total_expenses"]["canonical_sql"].format(**self.DATE_RANGE)
        res = sqlite_executor.execute_query(raw_sql)
        assert res.row_count == 1
        assert res.rows[0]["total_expenses"] == 45700.0
        assert res.rows[0]["expense_count"] == 5

    def test_metric_gross_profit(self, sqlite_executor: DatabaseExecutor, metastore: dict) -> None:
        raw_sql = metastore["calculated_metrics"]["gross_profit"]["canonical_sql"].format(**self.DATE_RANGE)
        res = sqlite_executor.execute_query(raw_sql)
        assert res.row_count == 1
        assert res.rows[0]["gross_profit"] == 2468.0
        assert res.rows[0]["total_sales_revenue"] == 10368.0
        assert res.rows[0]["cost_of_goods_sold"] == 7900.0

    def test_metric_net_profit(self, sqlite_executor: DatabaseExecutor, metastore: dict) -> None:
        raw_sql = metastore["calculated_metrics"]["net_profit"]["canonical_sql"].format(**self.DATE_RANGE)
        res = sqlite_executor.execute_query(raw_sql)
        assert res.row_count == 1
        assert res.rows[0]["net_profit"] == -43232.0
        assert res.rows[0]["gross_profit"] == 2468.0
        assert res.rows[0]["total_expenses"] == 45700.0

    def test_metric_party_unpaid_balance(self, sqlite_executor: DatabaseExecutor, metastore: dict) -> None:
        raw_sql = metastore["calculated_metrics"]["party_unpaid_balance"]["canonical_sql"]
        res = sqlite_executor.execute_query(raw_sql)
        assert res.row_count == 6
        first_row = res.rows[0]
        assert first_row["id"] == 2
        assert first_row["name"] == "BABA BARFANI"
        assert first_row["party_unpaid_balance"] == 39802.09
        second_row = res.rows[1]
        assert second_row["id"] == 68
        assert second_row["name"] == "Bharath"
        assert second_row["party_unpaid_balance"] == 24006.74

    def test_metric_total_purchases(self, sqlite_executor: DatabaseExecutor, metastore: dict) -> None:
        raw_sql = metastore["calculated_metrics"]["total_purchases"]["canonical_sql"].format(**self.DATE_RANGE)
        res = sqlite_executor.execute_query(raw_sql)
        assert res.row_count == 1
        assert res.rows[0]["total_purchases"] == 11045.0
        assert res.rows[0]["purchase_invoice_count"] == 2

    def test_metric_batch_stock_on_hand(self, sqlite_executor: DatabaseExecutor, metastore: dict) -> None:
        raw_sql = metastore["calculated_metrics"]["batch_stock_on_hand"]["canonical_sql"]
        res = sqlite_executor.execute_query(raw_sql)
        assert res.row_count == 14
        row_phelan = next(r for r in res.rows if r["goods_name"] == "Phelan Sherman")
        assert row_phelan["stock_on_hand"] == -5.0
        assert row_phelan["stock_value"] == -5000.0

    def test_empty_date_range_metrics(self, sqlite_executor: DatabaseExecutor, metastore: dict) -> None:
        empty_range = {"from_date": "2021-01-01", "to_date": "2021-12-31"}
        raw_sql = metastore["calculated_metrics"]["total_sales_revenue"]["canonical_sql"].format(**empty_range)
        res = sqlite_executor.execute_query(raw_sql)
        assert res.row_count == 1
        assert res.rows[0]["total_sales_revenue"] == 0.0
        assert res.rows[0]["sales_invoice_count"] == 0

    def test_transpilation_mysql_functions(self, sqlite_executor: DatabaseExecutor) -> None:
        # String concatenation
        res_concat = sqlite_executor.execute_query("SELECT CONCAT(name, ' (', role, ')') AS full_desc FROM accounts LIMIT 1")
        assert res_concat.row_count == 1
        assert "(" in res_concat.rows[0]["full_desc"]

        # Date functions
        res_date = sqlite_executor.execute_query("SELECT CURDATE() AS today, NOW() AS now_dt, YEAR('2026-11-02') AS yr, MONTH('2026-11-02') AS mo, DAY('2026-11-02') AS dy")
        assert res_date.row_count == 1
        assert res_date.rows[0]["yr"] == 2026
        assert res_date.rows[0]["mo"] == 11
        assert res_date.rows[0]["dy"] == 2

        # GREATEST and LEAST
        res_extremes = sqlite_executor.execute_query("SELECT GREATEST(10, 25, 5) AS gr, LEAST(10, 25, 5) AS ls")
        assert res_extremes.rows[0]["gr"] == 25
        assert res_extremes.rows[0]["ls"] == 5

        # DATE_SUB
        res_sub = sqlite_executor.execute_query("SELECT DATE_SUB('2026-10-31', INTERVAL 10 DAY) AS past_dt")
        assert res_sub.row_count == 1
        assert res_sub.rows[0]["past_dt"] == "2026-10-21"

    def test_decimal_rounding_normalizer(self, sqlite_executor: DatabaseExecutor) -> None:
        res = sqlite_executor.execute_query("SELECT 10.12345 AS val")
        assert res.rows[0]["val"] == 10.12


# --------------------------------------------------------------------------- #
# Class 3: TestDualModeAndFallbackSwitching
# --------------------------------------------------------------------------- #
class TestDualModeAndFallbackSwitching:
    def test_mode_detection_sqlite_when_no_url(self) -> None:
        settings = Settings(db_url=None, db_host=None, db_user=None)
        executor = DatabaseExecutor(settings=settings, mode="auto")
        assert executor.active_mode == "sqlite_fallback"
        assert executor.mode == "sqlite_fallback"
        res = executor.execute_query("SELECT 1 AS val")
        assert res.dialect == "sqlite"

    def test_mode_forced_sqlite(self) -> None:
        settings = Settings(db_url="mysql+pymysql://user:pass@127.0.0.1:3306/ledger")
        executor = DatabaseExecutor(settings=settings, mode="sqlite")
        assert executor.active_mode == "sqlite_fallback"
        assert executor.mode == "sqlite_fallback"

    def test_mode_detection_live_mysql_when_url_provided(self) -> None:
        settings = Settings(db_url="mysql+pymysql://user:pass@127.0.0.1:3306/ledger")
        executor = DatabaseExecutor(settings=settings, mode="auto")
        with patch.object(LiveMySQLEngine, "check_liveness", return_value=True):
            assert executor.resolve_mode(force_refresh=True) == "mysql"
            assert executor.active_mode == "live_mysql"

    def test_mode_forced_mysql_raises_when_offline(self) -> None:
        settings = Settings(db_url="mysql+pymysql://user:pass@127.0.0.1:3306/ledger")
        executor = DatabaseExecutor(settings=settings, mode="mysql")
        with patch.object(LiveMySQLEngine, "check_liveness", return_value=False):
            with pytest.raises(DatabaseConnectionError):
                executor.resolve_mode()

    def test_mocked_live_mysql_execution_success(self) -> None:
        settings = Settings(db_url="mysql+pymysql://user:pass@127.0.0.1:3306/ledger")
        executor = DatabaseExecutor(settings=settings, mode="mysql")
        with patch.object(LiveMySQLEngine, "check_liveness", return_value=True):
            mock_mysql = MagicMock()
            mock_mysql.execute_query.return_value = ExecutionResult(
                columns=["VoucherID", "TotalAmount"],
                rows=[{"VoucherID": 1, "TotalAmount": 5000.0}],
                execution_time_ms=12.5,
                row_count=1,
                dialect="mysql",
                source_mode="live_mysql",
            )
            with patch.object(executor, "_get_mysql_engine", return_value=mock_mysql):
                res = executor.execute_query("SELECT VoucherID, TotalAmount FROM voucher LIMIT 1")
                assert res.dialect == "mysql"
                assert res.source_mode == "live_mysql"
                assert res.row_count == 1
                assert res.rows[0]["TotalAmount"] == 5000.0

    def test_mocked_mysql_connection_failure_fallback_enabled(self) -> None:
        settings = Settings(db_url="mysql+pymysql://user:pass@127.0.0.1:3306/ledger")
        executor = DatabaseExecutor(settings=settings, mode="auto", auto_fallback=True)
        with patch.object(LiveMySQLEngine, "check_liveness", return_value=True):
            mock_mysql = MagicMock()
            mock_mysql.execute_query.side_effect = DatabaseConnectionError("Lost connection to MySQL")
            with patch.object(executor, "_get_mysql_engine", return_value=mock_mysql):
                res = executor.execute_query("SELECT VoucherID, TotalAmount FROM voucher LIMIT 1")
                assert res.dialect == "sqlite"
                assert res.source_mode == "sqlite_fallback"
                assert res.row_count == 1

    def test_mocked_mysql_connection_failure_fallback_disabled(self) -> None:
        settings = Settings(db_url="mysql+pymysql://user:pass@127.0.0.1:3306/ledger")
        executor = DatabaseExecutor(settings=settings, mode="mysql", auto_fallback=False)
        with patch.object(LiveMySQLEngine, "check_liveness", return_value=True):
            mock_mysql = MagicMock()
            mock_mysql.execute_query.side_effect = DatabaseConnectionError("Lost connection to MySQL")
            with patch.object(executor, "_get_mysql_engine", return_value=mock_mysql):
                with pytest.raises(DatabaseConnectionError):
                    executor.execute_query("SELECT 1")

    def test_mocked_mysql_statement_timeout(self) -> None:
        settings = Settings(db_url="mysql+pymysql://user:pass@127.0.0.1:3306/ledger")
        engine = LiveMySQLEngine(db_url=settings.db_url)
        mock_conn = MagicMock()
        # Simulate PyMySQL error 3024 (max_execution_time exceeded)
        orig_exc = Exception(3024, "Query execution was interrupted, max_statement_time exceeded")
        sa_exc = sqlalchemy.exc.OperationalError("SELECT SLEEP(10)", {}, orig_exc)
        mock_conn.execute.side_effect = sa_exc

        mock_eng = MagicMock()
        mock_eng.connect.return_value.__enter__.return_value = mock_conn
        engine._engine = mock_eng

        with pytest.raises(StatementTimeoutError) as exc_info:
            engine.execute_query("SELECT SLEEP(10)")
        assert exc_info.value.error_code == "TIMEOUT"
        assert exc_info.value.original_exception is not None

    def test_mariadb_statement_timeout(self) -> None:
        orig_exc = Exception(1969, "Query execution was interrupted, max_statement_time exceeded")
        sa_exc = sqlalchemy.exc.OperationalError("SELECT 1", {}, orig_exc)
        code, msg = classify_db_error(sa_exc)
        assert code == "TIMEOUT"


# --------------------------------------------------------------------------- #
# Class 4: TestErrorHandlingAndExceptionMapping
# --------------------------------------------------------------------------- #
class TestErrorHandlingAndExceptionMapping:
    def test_invalid_sql_syntax_raises_error(self, sqlite_executor: DatabaseExecutor) -> None:
        with pytest.raises(DatabaseExecutionError) as exc_info:
            sqlite_executor.execute_query("SELECT FROM voucher WHERE")
        assert exc_info.value.error_code in ("SYNTAX_ERROR", "EXECUTION_ERROR", "TRANSCRIPTION_ERROR")
        assert exc_info.value.original_exception is not None

    def test_non_existent_column_raises_error(self, sqlite_executor: DatabaseExecutor) -> None:
        with pytest.raises(DatabaseExecutionError) as exc_info:
            sqlite_executor.execute_query("SELECT phantom_column FROM voucher")
        assert "column" in str(exc_info.value).lower() or exc_info.value.error_code == "SCHEMA_ERROR"

    def test_non_existent_table_raises_error(self, sqlite_executor: DatabaseExecutor) -> None:
        with pytest.raises(DatabaseExecutionError) as exc_info:
            sqlite_executor.execute_query("SELECT * FROM ghost_table")
        assert "table" in str(exc_info.value).lower() or exc_info.value.error_code == "SCHEMA_ERROR"

    def test_sqlite_statement_timeout_via_progress_handler(self, dump_path: Path) -> None:
        engine = SQLiteDumpEngine(dump_path=dump_path, db_path=":memory:", timeout_s=0.05)
        infinite_sql = "WITH RECURSIVE cnt AS (SELECT 1 AS x UNION ALL SELECT x + 1 FROM cnt) SELECT * FROM cnt"
        with pytest.raises(StatementTimeoutError) as exc_info:
            engine.execute_query(infinite_sql)
        assert exc_info.value.error_code == "TIMEOUT"
        assert "timeout" in str(exc_info.value).lower()

    def test_division_by_zero_handling(self, sqlite_executor: DatabaseExecutor) -> None:
        res = sqlite_executor.execute_query("SELECT 1 / 0 AS res")
        assert res.row_count == 1
        assert res.rows[0]["res"] is None

        # Test classify_db_error with MySQL 1365
        orig_exc = Exception(1365, "Division by 0")
        sa_exc = sqlalchemy.exc.DataError("SELECT 1 / 0", {}, orig_exc)
        code, msg = classify_db_error(sa_exc)
        assert code == "DIVISION_BY_ZERO"

    def test_execution_result_envelope(self, sqlite_executor: DatabaseExecutor) -> None:
        res = sqlite_executor.execute_query("SELECT VoucherID, TotalAmount FROM voucher LIMIT 2")
        assert isinstance(res, ExecutionResult)
        assert res.columns == ["VoucherID", "TotalAmount"]
        assert isinstance(res.rows, list)
        assert len(res.rows) == 2
        assert isinstance(res.execution_time_ms, float)
        assert res.execution_time_ms >= 0.0
        assert res.row_count == 2

        d = res.to_dict()
        assert isinstance(d, dict)
        assert d["row_count"] == 2
        assert "columns" in d
        assert "rows" in d


# --------------------------------------------------------------------------- #
# Class 5: TestConcurrencyAndHelpers
# --------------------------------------------------------------------------- #
class TestConcurrencyAndHelpers:
    def test_concurrent_execution_thread_safety(self, sqlite_executor: DatabaseExecutor) -> None:
        def _run_query(idx: int) -> int:
            res = sqlite_executor.execute_query(f"SELECT COUNT(*) AS total, {idx} AS worker_id FROM accounts")
            return res.rows[0]["total"]

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(_run_query, i) for i in range(20)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        assert len(results) == 20
        assert all(count == 55 for count in results)

    def test_build_mysql_url_helper(self) -> None:
        # Standard parameters
        url = build_mysql_url(
            host="127.0.0.1",
            port=3306,
            user="ai_reader",
            password="p@ss/word#1",
            name="erp_ledger",
        )
        assert url == "mysql+pymysql://ai_reader:p%40ss%2Fword%231@127.0.0.1:3306/erp_ledger"

        # Explicit db_url override
        override_url = "mysql+pymysql://custom:root@remote/db"
        assert build_mysql_url(db_url=override_url) == override_url

        # Missing host or user returns None
        assert build_mysql_url(host=None, user="admin") is None

        # Settings helper
        s = Settings(
            db_host="localhost",
            db_user="reporter",
            db_password="secret",
            database_name="ledger",
        )
        assert s.build_mysql_url() == "mysql+pymysql://reporter:secret@localhost:3306/ledger"

    def test_normalize_value_edge_cases(self) -> None:
        assert normalize_value(None) is None
        assert normalize_value(b"test_bytes") == "test_bytes"
        assert normalize_value(Decimal("123.456")) == 123.46
        assert normalize_value(float("nan")) is None
        assert normalize_value(float("inf")) is None
        assert normalize_value(float("-inf")) is None
        assert normalize_value(12.3456) == 12.35
        assert normalize_value(datetime(2026, 11, 2, 0, 0, 0)) == "2026-11-02"
        assert normalize_value(datetime(2026, 11, 2, 14, 30, 0)) == "2026-11-02T14:30:00"
        assert normalize_value(date(2026, 11, 2)) == "2026-11-02"
        assert normalize_value("2026-10-15 00:00:00") == "2026-10-15"
        assert normalize_value("regular_text") == "regular_text"
        assert normalize_value(42) == 42


# --------------------------------------------------------------------------- #
# Class 6: TestReviewer2Remediation (Milestone 2 Iteration 2 Fixes)
# --------------------------------------------------------------------------- #
class TestReviewer2Remediation:
    """Verifies all 5 remediation fixes required by Reviewer 2."""

    def test_execute_query_timeout_ms_keyword_and_positional(
        self, sqlite_executor: DatabaseExecutor
    ) -> None:
        """Fix 1: execute_query handles keyword timeout_ms and positional integer timeout without TypeError."""
        # 1. Keyword invocation with timeout_ms
        res_kw = sqlite_executor.execute_query("SELECT 1 AS val", timeout_ms=3000)
        assert isinstance(res_kw, ExecutionResult)
        assert res_kw.dialect == "sqlite"
        assert res_kw.source_mode == "sqlite_fallback"
        assert res_kw.row_count == 1
        assert res_kw.rows[0]["val"] == 1
        assert isinstance(res_kw.execution_time_ms, float)

        # 2. Positional integer timeout_ms (when tenant_id is int)
        res_pos = sqlite_executor.execute_query("SELECT 2 AS val", 3000)
        assert isinstance(res_pos, ExecutionResult)
        assert res_pos.row_count == 1
        assert res_pos.rows[0]["val"] == 2

        # 3. Explicit tenant_id + keyword timeout_ms
        res_both_kw = sqlite_executor.execute_query(
            "SELECT 3 AS val", tenant_id="custom_tenant", timeout_ms=2500
        )
        assert res_both_kw.row_count == 1
        assert res_both_kw.rows[0]["val"] == 3

        # 4. Explicit tenant_id + positional timeout_ms
        res_both_pos = sqlite_executor.execute_query("SELECT 4 AS val", "custom_tenant", 2500)
        assert res_both_pos.row_count == 1
        assert res_both_pos.rows[0]["val"] == 4

        # 5. Default backward-compatible invocation (no timeout_ms)
        res_default = sqlite_executor.execute_query("SELECT 5 AS val")
        assert res_default.row_count == 1
        assert res_default.rows[0]["val"] == 5

        # 6. Timeout enforcement: slow query with short timeout_ms raises StatementTimeoutError
        infinite_sql = "WITH RECURSIVE cnt AS (SELECT 1 AS x UNION ALL SELECT x + 1 FROM cnt) SELECT * FROM cnt"
        with pytest.raises(StatementTimeoutError) as exc_info:
            sqlite_executor.execute_query(infinite_sql, timeout_ms=50)
        assert exc_info.value.error_code == "TIMEOUT"

    def test_regexp_like_transpilation_and_execution(
        self, sqlite_executor: DatabaseExecutor
    ) -> None:
        """Fix 2: MySQL REGEXP query transpiles to REGEXP_LIKE and executes on SQLite without missing function error."""
        raw_mysql_sql = "SELECT name FROM accounts WHERE name REGEXP '^B.*' LIMIT 2"

        # 1. Verify SQLGlot transpiles REGEXP to REGEXP_LIKE
        transpiled = transpile_mysql_to_sqlite(raw_mysql_sql)
        assert "REGEXP_LIKE" in transpiled

        # 2. Verify successful execution against SQLite seed accounts data
        res = sqlite_executor.execute_query(raw_mysql_sql)
        assert isinstance(res, ExecutionResult)
        assert res.dialect == "sqlite"
        assert res.source_mode == "sqlite_fallback"
        assert res.row_count == 2
        for row in res.rows:
            assert row["name"].startswith("B")

        # 3. Verify non-matching regex filter returns 0 rows safely
        no_match_sql = "SELECT name FROM accounts WHERE name REGEXP '^NON_EXISTENT_PREFIX.*'"
        res_empty = sqlite_executor.execute_query(no_match_sql)
        assert res_empty.row_count == 0

        # 4. Verify direct scalar REGEXP_LIKE evaluations
        scalar_sql = "SELECT REGEXP_LIKE('ERP System', '^ERP') AS m1, REGEXP_LIKE('CRM System', '^ERP') AS m2"
        res_scalar = sqlite_executor.execute_query(scalar_sql)
        assert res_scalar.row_count == 1
        assert res_scalar.rows[0]["m1"] == 1
        assert res_scalar.rows[0]["m2"] == 0

        # 5. Verify NULL safety in REGEXP_LIKE
        null_sql = "SELECT REGEXP_LIKE(NULL, '^A') AS n1, REGEXP_LIKE('ABC', NULL) AS n2"
        res_null = sqlite_executor.execute_query(null_sql)
        assert res_null.rows[0]["n1"] == 0
        assert res_null.rows[0]["n2"] == 0

    def test_connection_drop_fallback_lost_connection(self) -> None:
        """Fix 3: 'Lost connection' string triggers CONNECTION_ERROR and transparently falls back to SQLite."""
        # 1. Verify classify_db_error classifies "Lost connection" as CONNECTION_ERROR
        drop_exc = Exception("Lost connection to MySQL server during query")
        code, msg = classify_db_error(drop_exc)
        assert code == "CONNECTION_ERROR"
        assert "lost" in msg.lower() or "connection" in msg.lower()

        # Additional mid-query disconnect pattern tests
        assert classify_db_error(Exception("Connection reset by peer"))[0] == "CONNECTION_ERROR"
        assert classify_db_error(Exception("[Errno 32] Broken pipe"))[0] == "CONNECTION_ERROR"
        assert classify_db_error(Exception("Communication link failure during execution"))[0] == "CONNECTION_ERROR"
        assert classify_db_error(Exception("Connection closed by server"))[0] == "CONNECTION_ERROR"

        # 2. Transparent fallback in DatabaseExecutor under auto mode
        settings = Settings(db_url="mysql+pymysql://user:pass@127.0.0.1:3306/ledger")
        executor = DatabaseExecutor(settings=settings, mode="auto", auto_fallback=True)

        with patch.object(LiveMySQLEngine, "check_liveness", return_value=True):
            mock_eng = MagicMock()
            mock_eng.connect.side_effect = Exception("Lost connection to MySQL server during query")
            executor._get_mysql_engine()._engine = mock_eng

            # Query must catch DatabaseConnectionError and fallback to SQLite
            res = executor.execute_query("SELECT count(*) as c FROM accounts")
            assert res.source_mode == "sqlite_fallback"
            assert res.dialect == "sqlite"
            assert res.row_count == 1
            assert res.rows[0]["c"] == 55
            assert executor.active_mode == "sqlite_fallback"

        # 3. Verify strict mode without fallback raises DatabaseConnectionError
        exec_strict = DatabaseExecutor(settings=settings, mode="mysql", auto_fallback=False)
        with patch.object(LiveMySQLEngine, "check_liveness", return_value=True):
            mock_eng_strict = MagicMock()
            mock_eng_strict.connect.side_effect = Exception("Lost connection to MySQL server during query")
            exec_strict._get_mysql_engine()._engine = mock_eng_strict

            with pytest.raises(DatabaseConnectionError):
                exec_strict.execute_query("SELECT count(*) as c FROM accounts")

    def test_lock_wait_does_not_consume_statement_timeout(self, dump_path: Path) -> None:
        """Fix 4: Lock waiting time under concurrency does not falsely consume statement timeout."""
        engine = SQLiteDumpEngine(dump_path=dump_path, db_path=":memory:", timeout_s=0.15)
        engine.provision()

        lock_acquired = threading.Event()
        release_blocker = threading.Event()

        def _lock_holder():
            with engine._lock:
                lock_acquired.set()
                # Hold lock until release_blocker is signalled or timeout
                release_blocker.wait(timeout=2.0)

        holder_thread = threading.Thread(target=_lock_holder)
        holder_thread.start()
        assert lock_acquired.wait(timeout=1.0), "Holder thread failed to acquire lock"

        def _delayed_releaser():
            # Release lock after 250ms (0.25s), which exceeds the 150ms timeout
            time.sleep(0.25)
            release_blocker.set()

        releaser_thread = threading.Thread(target=_delayed_releaser)
        releaser_thread.start()

        # Query executes in < 1ms but produces 24,750 combinations (~52 progress checks).
        # It waits 250ms for lock release before executing.
        # Since timeout_s=0.15s (150ms), if lock wait leaked, this would immediately raise StatementTimeoutError.
        query = "SELECT count(*) AS total FROM accounts CROSS JOIN categories CROSS JOIN batches"
        res = engine.execute_query(query, timeout_s=0.15)
        assert res.row_count == 1
        assert res.rows[0]["total"] == 24750

        holder_thread.join()
        releaser_thread.join()

        # Verify genuine statement timeout enforcement is preserved
        infinite_sql = "WITH RECURSIVE cnt AS (SELECT 1 AS x UNION ALL SELECT x + 1 FROM cnt) SELECT * FROM cnt"
        with pytest.raises(StatementTimeoutError) as exc_info:
            engine.execute_query(infinite_sql, timeout_s=0.05)
        assert exc_info.value.error_code == "TIMEOUT"

    def test_date_sub_negative_interval(self, sqlite_executor: DatabaseExecutor) -> None:
        """Fix 5: DATE_SUB with negative interval correctly adds time instead of subtracting."""
        # 1. Transpilation check: negative interval becomes '+1 month' or '1 month', not '-1 month'
        sql_neg_month = "SELECT DATE_SUB('2026-11-02', INTERVAL -1 MONTH) AS next_month"
        transpiled = transpile_mysql_to_sqlite(sql_neg_month)
        assert "-1 month" not in transpiled.lower()
        assert "1 month" in transpiled.lower()

        # 2. Execution check: Subtracting -1 MONTH adds 1 month (2026-11-02 -> 2026-12-02)
        res_month = sqlite_executor.execute_query(sql_neg_month)
        assert res_month.row_count == 1
        assert res_month.rows[0]["next_month"] == "2026-12-02"

        # 3. Negative day interval: Subtracting -10 DAY adds 10 days (2026-11-02 -> 2026-11-12)
        res_days = sqlite_executor.execute_query(
            "SELECT DATE_SUB('2026-11-02', INTERVAL -10 DAY) AS future_dt"
        )
        assert res_days.row_count == 1
        assert res_days.rows[0]["future_dt"] == "2026-11-12"

        # 4. Negative year interval: Subtracting -2 YEAR adds 2 years (2026-11-02 -> 2028-11-02)
        res_yr = sqlite_executor.execute_query(
            "SELECT DATE_SUB('2026-11-02', INTERVAL -2 YEAR) AS future_yr"
        )
        assert res_yr.row_count == 1
        assert res_yr.rows[0]["future_yr"] == "2028-11-02"

        # 5. Regression check: Positive interval continues to subtract properly
        res_pos = sqlite_executor.execute_query(
            "SELECT DATE_SUB('2026-11-02', INTERVAL 1 MONTH) AS past_month"
        )
        assert res_pos.row_count == 1
        assert res_pos.rows[0]["past_month"] == "2026-10-02"

        # 6. Complementary DATE_ADD with negative interval
        res_add_neg = sqlite_executor.execute_query(
            "SELECT DATE_ADD('2026-11-02', INTERVAL -1 MONTH) AS past_month"
        )
        assert res_add_neg.row_count == 1
        assert res_add_neg.rows[0]["past_month"] == "2026-10-02"


# Also alias TestMilestone2Iteration2Remediation for test harness compatibility
TestMilestone2Iteration2Remediation = TestReviewer2Remediation
