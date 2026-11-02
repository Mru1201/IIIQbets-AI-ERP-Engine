"""Adversarial stress-test suite for Milestone 2 Dual-Mode DB Execution Engine.

Tests written by Challenger 1 to stress-test:
1. Statement Timeout Enforcement (recursive CTEs, heavy cartesian joins, post-timeout recovery, MySQL/MariaDB timeout codes)
2. SQL Syntax & Domain Exception Mapping (malformed queries, TableNotFoundError, ColumnNotFoundError, Division by Zero)
3. Edge Case Tables (Table without PK 'expensive', 0-row tables 'orders' and 'order_items', negative values in 'batches')
4. SQLGlot Transpilation Edge Cases (DATE_SUB with various units and column intervals, NOW/CURDATE, CONCAT/CONCAT_WS, nested subqueries, backticks, comments)
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy.exc

from app.config import DEFAULT_SQL_DUMP_PATH, Settings
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


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def dump_path() -> Path:
    return DEFAULT_SQL_DUMP_PATH


@pytest.fixture
def sqlite_executor(dump_path: Path) -> DatabaseExecutor:
    return DatabaseExecutor(
        db_url=None,
        mode="sqlite",
        sql_dump_path=dump_path,
        statement_timeout_ms=5000,
        auto_fallback=True,
    )


# --------------------------------------------------------------------------- #
# 1. Statement Timeout Enforcement Stress Tests
# --------------------------------------------------------------------------- #
class TestAdversarialTimeoutEnforcement:
    def test_sqlite_statement_timeout_infinite_cte(self, dump_path: Path) -> None:
        """Verify recursive CTE exceeding wall-clock timeout raises StatementTimeoutError."""
        low_timeout_executor = DatabaseExecutor(
            mode="sqlite",
            sql_dump_path=dump_path,
            statement_timeout_ms=100,  # 100ms
        )
        infinite_sql = "WITH RECURSIVE cnt AS (SELECT 1 AS x UNION ALL SELECT x + 1 FROM cnt) SELECT count(*) FROM cnt"
        with pytest.raises(StatementTimeoutError) as exc_info:
            low_timeout_executor.execute_query(infinite_sql)

        err = exc_info.value
        assert isinstance(err, StatementTimeoutError)
        assert isinstance(err, DatabaseExecutionError)
        assert err.error_code == "TIMEOUT"
        assert "timeout" in err.message.lower()
        assert err.original_exception is not None

    def test_sqlite_statement_timeout_heavy_cartesian_join(self, dump_path: Path) -> None:
        """Verify heavy cross join (55^5 = 503M rows) exceeding timeout raises StatementTimeoutError."""
        low_timeout_executor = DatabaseExecutor(
            mode="sqlite",
            sql_dump_path=dump_path,
            statement_timeout_ms=100,
        )
        heavy_sql = "SELECT count(*) FROM accounts a1, accounts a2, accounts a3, accounts a4, accounts a5"
        with pytest.raises(StatementTimeoutError) as exc_info:
            low_timeout_executor.execute_query(heavy_sql)

        assert exc_info.value.error_code == "TIMEOUT"

    def test_sqlite_clean_recovery_after_timeout(self, dump_path: Path) -> None:
        """Verify the progress handler is cleared and executor recovers cleanly after a timeout."""
        executor = DatabaseExecutor(
            mode="sqlite",
            sql_dump_path=dump_path,
            statement_timeout_ms=100,
        )
        heavy_sql = "SELECT count(*) FROM accounts a1, accounts a2, accounts a3, accounts a4, accounts a5"
        with pytest.raises(StatementTimeoutError):
            executor.execute_query(heavy_sql)

        # Subsequent fast query on the exact same executor instance must succeed without hang
        res = executor.execute_query("SELECT count(*) AS total FROM accounts")
        assert res.row_count == 1
        assert res.rows[0]["total"] == 55

    def test_mysql_and_mariadb_timeout_error_codes(self) -> None:
        """Verify classify_db_error correctly maps MySQL 3024, MariaDB 1969, and interrupted 1317."""
        # MySQL code 3024
        exc_3024 = sqlalchemy.exc.OperationalError(
            "SELECT 1", {}, Exception(3024, "Query execution was interrupted, max_statement_time exceeded")
        )
        code, msg = classify_db_error(exc_3024)
        assert code == "TIMEOUT"

        # MariaDB code 1969
        exc_1969 = sqlalchemy.exc.OperationalError(
            "SELECT 1", {}, Exception(1969, "Query execution was interrupted, max_statement_time exceeded")
        )
        code, msg = classify_db_error(exc_1969)
        assert code == "TIMEOUT"

        # Interrupted code 1317
        exc_1317 = sqlalchemy.exc.OperationalError(
            "SELECT 1", {}, Exception(1317, "Query execution was interrupted")
        )
        code, msg = classify_db_error(exc_1317)
        assert code == "TIMEOUT"

        # LiveMySQLEngine execution wrapping
        engine = LiveMySQLEngine(db_url="mysql+pymysql://user:pass@127.0.0.1:3306/db")
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = exc_3024
        mock_eng = MagicMock()
        mock_eng.connect.return_value.__enter__.return_value = mock_conn
        engine._engine = mock_eng

        with pytest.raises(StatementTimeoutError) as exc_info:
            engine.execute_query("SELECT 1")
        assert exc_info.value.error_code == "TIMEOUT"


# --------------------------------------------------------------------------- #
# 2. SQL Syntax & Error Handling Stress Tests
# --------------------------------------------------------------------------- #
class TestAdversarialSyntaxAndErrorHandling:
    @pytest.mark.parametrize(
        "bad_sql",
        [
            "SELECT * FROM",
            "SELECT FROM WHERE",
            "SELECT * FROM voucher WHERE (",
            "SELECT * FROM accounts WHERE id =",
            "SELECT 1 + * 2",
            "SELEC * FROM accounts",
            "SELECT * FROM accounts ORDER BY",
        ],
    )
    def test_invalid_sql_syntax_raises_database_syntax_error(
        self, sqlite_executor: DatabaseExecutor, bad_sql: str
    ) -> None:
        """Verify malformed queries raise DatabaseSyntaxError with code SYNTAX_ERROR."""
        with pytest.raises(DatabaseSyntaxError) as exc_info:
            sqlite_executor.execute_query(bad_sql)
        assert exc_info.value.error_code == "SYNTAX_ERROR"
        assert isinstance(exc_info.value, DatabaseExecutionError)

    @pytest.mark.parametrize(
        "missing_table_sql",
        [
            "SELECT * FROM non_existent_table_xyz",
            "SELECT * FROM accounts a JOIN missing_table_123 b ON a.id = b.id",
            "SELECT * FROM (SELECT * FROM subquery_missing_tbl) sub",
        ],
    )
    def test_missing_table_raises_table_not_found_error(
        self, sqlite_executor: DatabaseExecutor, missing_table_sql: str
    ) -> None:
        """Verify non-existent tables raise TableNotFoundError."""
        with pytest.raises(TableNotFoundError) as exc_info:
            sqlite_executor.execute_query(missing_table_sql)
        assert exc_info.value.error_code == "SCHEMA_ERROR"
        assert isinstance(exc_info.value, DatabaseExecutionError)
        assert "table" in str(exc_info.value).lower()

    @pytest.mark.parametrize(
        "missing_col_sql",
        [
            "SELECT ghost_col FROM accounts",
            "SELECT * FROM accounts WHERE phantom_col = 1",
            "SELECT a.id, a.invalid_column_name FROM accounts a",
            "SELECT COUNT(fake_col) FROM accounts",
        ],
    )
    def test_missing_column_raises_column_not_found_error(
        self, sqlite_executor: DatabaseExecutor, missing_col_sql: str
    ) -> None:
        """Verify non-existent columns raise ColumnNotFoundError."""
        with pytest.raises(ColumnNotFoundError) as exc_info:
            sqlite_executor.execute_query(missing_col_sql)
        assert exc_info.value.error_code == "SCHEMA_ERROR"
        assert isinstance(exc_info.value, DatabaseExecutionError)
        assert "column" in str(exc_info.value).lower()

    def test_division_by_zero_evaluation(self, sqlite_executor: DatabaseExecutor) -> None:
        """Verify division by zero produces None in SQLite without crashing."""
        res_int = sqlite_executor.execute_query("SELECT 100 / 0 AS div_zero")
        assert res_int.row_count == 1
        assert res_int.rows[0]["div_zero"] is None

        res_float = sqlite_executor.execute_query("SELECT 150.5 / 0.0 AS div_float_zero")
        assert res_float.row_count == 1
        assert res_float.rows[0]["div_float_zero"] is None

    def test_mysql_schema_and_connection_error_mapping(self) -> None:
        """Verify classify_db_error correctly classifies MySQL DBAPI codes."""
        # 1146 -> Table doesn't exist
        exc_1146 = sqlalchemy.exc.ProgrammingError(
            "SELECT * FROM ghost", {}, Exception(1146, "Table 'erp.ghost' doesn't exist")
        )
        code, msg = classify_db_error(exc_1146)
        assert code == "SCHEMA_ERROR"
        assert "table" in msg.lower()

        # 1054 -> Unknown column
        exc_1054 = sqlalchemy.exc.ProgrammingError(
            "SELECT ghost FROM accounts", {}, Exception(1054, "Unknown column 'ghost' in 'field list'")
        )
        code, msg = classify_db_error(exc_1054)
        assert code == "SCHEMA_ERROR"
        assert "column" in msg.lower()

        # 1064 -> Syntax error
        exc_1064 = sqlalchemy.exc.ProgrammingError(
            "SELEC 1", {}, Exception(1064, "You have an error in your SQL syntax near 'SELEC 1'")
        )
        code, msg = classify_db_error(exc_1064)
        assert code == "SYNTAX_ERROR"

        # 2003 -> Can't connect to server
        exc_2003 = sqlalchemy.exc.OperationalError(
            "SELECT 1", {}, Exception(2003, "Can't connect to MySQL server on '127.0.0.1'")
        )
        code, msg = classify_db_error(exc_2003)
        assert code == "CONNECTION_ERROR"

        # 1045 -> Access denied
        exc_1045 = sqlalchemy.exc.OperationalError(
            "SELECT 1", {}, Exception(1045, "Access denied for user 'root'@'localhost'")
        )
        code, msg = classify_db_error(exc_1045)
        assert code == "CONNECTION_ERROR"


# --------------------------------------------------------------------------- #
# 3. Edge Case Tables & Data Tests
# --------------------------------------------------------------------------- #
class TestAdversarialEdgeCaseTables:
    def test_expensive_table_no_pk_full_lifecycle(self, sqlite_executor: DatabaseExecutor) -> None:
        """Stress-test table 'expensive' which has NO primary key in dump."""
        sqlite_executor.provision_sqlite()
        # Check table info
        engine = sqlite_executor._get_sqlite_engine()
        assert engine._conn is not None
        cur = engine._conn.cursor()
        cur.execute('PRAGMA table_info("expensive")')
        cols = {row[1]: row[5] for row in cur.fetchall()}
        assert len(cols) == 10
        assert all(pk_flag == 0 for pk_flag in cols.values()), "expensive must have no primary key"

        # Read all rows
        res = sqlite_executor.execute_query("SELECT * FROM expensive")
        assert res.row_count == 5
        assert len(res.rows) == 5

        # Group by and aggregate
        res_agg = sqlite_executor.execute_query(
            "SELECT category, SUM(amount) AS total_amount, AVG(amount) AS avg_amount, COUNT(*) AS count_items "
            "FROM expensive GROUP BY category ORDER BY total_amount DESC"
        )
        assert res_agg.row_count == 2
        petrol = res_agg.rows[0]
        assert petrol["category"] == "Petrol"
        assert petrol["total_amount"] == 23700
        assert petrol["avg_amount"] == 7900.0
        assert petrol["count_items"] == 3

        # Filter by amount range
        res_filtered = sqlite_executor.execute_query("SELECT * FROM expensive WHERE amount >= 20000")
        assert res_filtered.row_count == 2
        assert all(r["amount"] >= 20000 for r in res_filtered.rows)

    def test_zero_row_tables_orders_and_order_items(self, sqlite_executor: DatabaseExecutor) -> None:
        """Stress-test querying empty tables 'orders' and 'order_items'."""
        # Direct select
        res_orders = sqlite_executor.execute_query("SELECT * FROM orders")
        assert res_orders.row_count == 0
        assert res_orders.rows == []
        assert len(res_orders.columns) == 27

        res_items = sqlite_executor.execute_query("SELECT * FROM order_items")
        assert res_items.row_count == 0
        assert res_items.rows == []
        assert len(res_items.columns) == 39

        # Join between empty tables
        res_join = sqlite_executor.execute_query(
            "SELECT o.order_number, oi.item_name FROM orders o "
            "JOIN order_items oi ON o.order_number = oi.order_number"
        )
        assert res_join.row_count == 0
        assert res_join.rows == []

        # Aggregations on empty table evaluate to 0 count and None sum/avg
        res_agg = sqlite_executor.execute_query(
            "SELECT COUNT(*) AS total_count, SUM(order_total) AS total_sum, AVG(order_total) AS total_avg "
            "FROM orders"
        )
        assert res_agg.row_count == 1
        assert res_agg.rows[0]["total_count"] == 0
        assert res_agg.rows[0]["total_sum"] is None
        assert res_agg.rows[0]["total_avg"] is None

        # Subquery referencing empty table
        res_sub = sqlite_executor.execute_query(
            "SELECT id, name FROM accounts WHERE id IN (SELECT customer_id FROM orders)"
        )
        assert res_sub.row_count == 0
        assert res_sub.rows == []

    def test_negative_values_batches_and_stock(self, sqlite_executor: DatabaseExecutor) -> None:
        """Verify negative stock values and valuations are correctly queried and normalized."""
        res = sqlite_executor.execute_query(
            "SELECT b.id, p.goods_name, CAST(b.quantity AS REAL) AS qty, CAST(b.cost_price AS REAL) AS cost "
            "FROM batches b JOIN products p ON b.product_id = p.id WHERE CAST(b.quantity AS REAL) < 0"
        )
        assert res.row_count == 1
        neg_row = res.rows[0]
        assert neg_row["goods_name"] == "Phelan Sherman"
        assert neg_row["qty"] == -5.0

        # Calculated metric batch_stock_on_hand produces negative valuation for negative quantity batch
        res_stock = sqlite_executor.execute_query(
            "SELECT p.goods_name, b.batch_number, "
            "CAST(b.quantity AS DECIMAL(15,2)) as stock_on_hand, "
            "CAST(b.quantity AS DECIMAL(15,2)) * CAST(b.purchase_price AS DECIMAL(15,2)) as stock_value "
            "FROM batches b JOIN products p ON b.product_id = p.id WHERE p.goods_name = 'Phelan Sherman' "
            "AND CAST(b.quantity AS REAL) < 0"
        )
        assert res_stock.row_count == 1
        assert res_stock.rows[0]["stock_on_hand"] == -5.0
        assert res_stock.rows[0]["stock_value"] == -5000.0


# --------------------------------------------------------------------------- #
# 4. SQLGlot Transpilation Edge Case Tests
# --------------------------------------------------------------------------- #
class TestAdversarialSQLGlotTranspilation:
    def test_date_sub_with_intervals_and_units(self, sqlite_executor: DatabaseExecutor) -> None:
        """Verify DATE_SUB works across days, months, years, and negative offsets."""
        queries = [
            ("SELECT DATE_SUB('2026-11-02', INTERVAL 10 DAY) AS d", "2026-10-23"),
            ("SELECT DATE_SUB('2026-11-02', INTERVAL 1 MONTH) AS d", "2026-10-02"),
            ("SELECT DATE_SUB('2026-11-02', INTERVAL 1 YEAR) AS d", "2025-11-02"),
        ]
        for sql, expected in queries:
            res = sqlite_executor.execute_query(sql)
            assert res.rows[0]["d"] == expected

    def test_date_sub_with_now_and_curdate(self, sqlite_executor: DatabaseExecutor) -> None:
        """Verify DATE_SUB combined with NOW() and CURDATE() executes successfully."""
        res_now = sqlite_executor.execute_query("SELECT DATE_SUB(NOW(), INTERVAL 7 DAY) AS past_date")
        assert res_now.row_count == 1
        past_str = res_now.rows[0]["past_date"]
        assert len(past_str) == 10
        assert past_str.startswith("20")

        res_cur = sqlite_executor.execute_query("SELECT DATE_SUB(CURDATE(), INTERVAL 14 DAY) AS past_date")
        assert res_cur.row_count == 1
        assert len(res_cur.rows[0]["past_date"]) == 10

    def test_date_sub_with_column_reference(self, sqlite_executor: DatabaseExecutor) -> None:
        """Verify DATE_SUB with a column expression (INTERVAL id DAY) transpiles and computes."""
        res = sqlite_executor.execute_query(
            "SELECT id, DATE_SUB('2026-11-02', INTERVAL id DAY) AS computed_dt FROM accounts WHERE id = 5"
        )
        assert res.row_count == 1
        assert res.rows[0]["computed_dt"] == "2026-10-28"

    def test_concat_behavior_and_null_handling(self, sqlite_executor: DatabaseExecutor) -> None:
        """Verify CONCAT multi-arg and NULL handling matches standard SQL behavior."""
        # Multi-argument concatenation
        res_multi = sqlite_executor.execute_query("SELECT CONCAT('A', 'B', 'C', 'D') AS res")
        assert res_multi.rows[0]["res"] == "ABCD"

        # Concatenation with NULL evaluates to None (standard MySQL behavior)
        res_null = sqlite_executor.execute_query("SELECT CONCAT('Hello', NULL, 'World') AS res")
        assert res_null.rows[0]["res"] is None

        # CONCAT_WS with separator skips NULLs
        res_ws = sqlite_executor.execute_query("SELECT CONCAT_WS('-', 'A', NULL, 'B', 'C') AS res")
        assert res_ws.rows[0]["res"] == "A-B-C"

    def test_nested_subqueries_and_backticks(self, sqlite_executor: DatabaseExecutor) -> None:
        """Verify deeply nested subqueries with MySQL backticks transpile and execute."""
        sql = (
            "SELECT `outer_tbl`.`category`, `outer_tbl`.`sub_total` FROM ("
            "  SELECT `inner_tbl`.`category`, SUM(`inner_tbl`.`amount`) AS `sub_total` FROM ("
            "    SELECT `id`, `category`, `amount` FROM `expensive` WHERE `status` = 'approved'"
            "  ) AS `inner_tbl` GROUP BY `inner_tbl`.`category`"
            ") AS `outer_tbl` WHERE `outer_tbl`.`sub_total` > 22000"
        )
        res = sqlite_executor.execute_query(sql)
        assert res.row_count == 1
        assert res.rows[0]["category"] == "Petrol"
        assert res.rows[0]["sub_total"] == 22200.0

    def test_sql_comments_and_semicolons(self, sqlite_executor: DatabaseExecutor) -> None:
        """Verify SQL queries with block comments, line comments, and trailing semicolons succeed."""
        sql = (
            "-- High priority report\n"
            "SELECT id, name /* internal customer identifier */ FROM accounts "
            "WHERE role = 'retailer' LIMIT 2;"
        )
        res = sqlite_executor.execute_query(sql)
        assert res.row_count == 2
        assert "id" in res.rows[0]
        assert "name" in res.rows[0]

    def test_case_when_conditional_expression(self, sqlite_executor: DatabaseExecutor) -> None:
        """Verify CASE WHEN logic executes properly."""
        sql = (
            "SELECT category, "
            "CASE WHEN amount > 20000 THEN 'MAJOR' ELSE 'MINOR' END AS expense_type "
            "FROM expensive ORDER BY id ASC LIMIT 3"
        )
        res = sqlite_executor.execute_query(sql)
        assert res.row_count == 3
        # Id 1: amount 22000 -> MAJOR
        assert res.rows[0]["expense_type"] == "MAJOR"
        # Id 2: amount 1200 -> MINOR
        assert res.rows[1]["expense_type"] == "MINOR"
