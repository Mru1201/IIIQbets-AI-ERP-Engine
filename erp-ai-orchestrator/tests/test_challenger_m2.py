"""Challenger 2 Empirical Verification & Adversarial Stress Suite for Milestone 2."""

import concurrent.futures
from decimal import Decimal
import time
from unittest.mock import MagicMock, patch
import pytest

from app.config import DEFAULT_SQL_DUMP_PATH, Settings
from app.connectors.executor import (
    DatabaseConnectionError,
    DatabaseExecutionError,
    DatabaseExecutor,
    DatabaseSyntaxError,
    ExecutionResult,
    LiveMySQLEngine,
    StatementTimeoutError,
    TableNotFoundError,
)
from app.metastore.loader import load_metastore


@pytest.fixture(scope="module")
def metastore():
    return load_metastore()


@pytest.fixture
def executor():
    return DatabaseExecutor(mode="sqlite")


# ============================================================================
# Section 1: Ground Truth Verification (8 Canonical Metrics)
# ============================================================================
class TestGroundTruthCanonicalMetrics:
    DATE_RANGE = {"from_date": "2020-01-01", "to_date": "2030-12-31"}

    def test_metric_1_total_sales_revenue(self, executor, metastore):
        sql = metastore["calculated_metrics"]["total_sales_revenue"]["canonical_sql"].format(**self.DATE_RANGE)
        res = executor.execute_query(sql)
        assert res.row_count == 1
        row = res.rows[0]
        assert row["total_sales_revenue"] == 10368.00
        assert row["sales_invoice_count"] == 3

    def test_metric_2_cost_of_goods_sold(self, executor, metastore):
        sql = metastore["calculated_metrics"]["cost_of_goods_sold"]["canonical_sql"].format(**self.DATE_RANGE)
        res = executor.execute_query(sql)
        assert res.row_count == 1
        row = res.rows[0]
        assert row["cost_of_goods_sold"] == 7900.00
        assert row["total_units_sold"] == 7.00

    def test_metric_3_total_expenses(self, executor, metastore):
        sql = metastore["calculated_metrics"]["total_expenses"]["canonical_sql"].format(**self.DATE_RANGE)
        res = executor.execute_query(sql)
        assert res.row_count == 1
        row = res.rows[0]
        assert row["total_expenses"] == 45700.00
        assert row["expense_count"] == 5

    def test_metric_4_gross_profit(self, executor, metastore):
        sql = metastore["calculated_metrics"]["gross_profit"]["canonical_sql"].format(**self.DATE_RANGE)
        res = executor.execute_query(sql)
        assert res.row_count == 1
        row = res.rows[0]
        assert row["gross_profit"] == 2468.00
        assert row["total_sales_revenue"] == 10368.00
        assert row["cost_of_goods_sold"] == 7900.00

    def test_metric_5_net_profit(self, executor, metastore):
        sql = metastore["calculated_metrics"]["net_profit"]["canonical_sql"].format(**self.DATE_RANGE)
        res = executor.execute_query(sql)
        assert res.row_count == 1
        row = res.rows[0]
        assert row["net_profit"] == -43232.00
        assert row["gross_profit"] == 2468.00
        assert row["total_expenses"] == 45700.00

    def test_metric_6_party_unpaid_balance_exact_ground_truth(self, executor, metastore):
        sql = metastore["calculated_metrics"]["party_unpaid_balance"]["canonical_sql"]
        res = executor.execute_query(sql)
        # 1. Exactly 6 debtors
        assert res.row_count == 6
        assert len(res.rows) == 6

        # 2. Top debtor: BABA BARFANI @ 39,802.09 INR
        top_debtor = res.rows[0]
        assert top_debtor["name"] == "BABA BARFANI"
        assert top_debtor["party_unpaid_balance"] == 39802.09

        # 3. Total debt sum: exactly 88,762.73 INR
        total_debt = sum(r["party_unpaid_balance"] for r in res.rows)
        assert round(total_debt, 2) == 88762.73

        # Verify all 6 debtor balances
        expected_debtors = [
            ("BABA BARFANI", 39802.09),
            ("Bharath", 24006.74),
            ("Bharathuniq", 9413.32),
            ("Sai", 7100.62),
            ("John Doe", 5943.96),
            ("Amar", 2496.00),
        ]
        actual_debtors = [(r["name"], r["party_unpaid_balance"]) for r in res.rows]
        assert actual_debtors == expected_debtors

    def test_metric_7_total_purchases(self, executor, metastore):
        sql = metastore["calculated_metrics"]["total_purchases"]["canonical_sql"].format(**self.DATE_RANGE)
        res = executor.execute_query(sql)
        assert res.row_count == 1
        row = res.rows[0]
        assert row["total_purchases"] == 11045.00
        assert row["purchase_invoice_count"] == 2

    def test_metric_8_batch_stock_on_hand_excludes_orphan_batch(self, executor, metastore):
        sql = metastore["calculated_metrics"]["batch_stock_on_hand"]["canonical_sql"]
        res = executor.execute_query(sql)
        # Exactly 14 rows, batch_number of orphan batch 20 (referencing non-existent product_id 15) must be excluded
        assert res.row_count == 14
        batch_numbers = [r["batch_number"] for r in res.rows]
        # In dump, batch id=15 has batch_number "BATCH015", etc. Orphan batch id=20 is not present.
        assert len(batch_numbers) == 14
        # Verify negative valuation is preserved accurately
        phelan_batch = next(r for r in res.rows if r["goods_name"] == "Phelan Sherman")
        assert phelan_batch["stock_on_hand"] == -5.0
        assert phelan_batch["stock_value"] == -5000.0


# ============================================================================
# Section 2: Concurrency Stress Test (20+ Concurrent Threads Mixed Queries)
# ============================================================================
class TestConcurrencyStressHarness:
    def test_concurrent_mixed_queries_30_threads(self, executor, metastore):
        date_range = {"from_date": "2020-01-01", "to_date": "2030-12-31"}
        queries = [
            # 0: Gross Profit (complex CTE + joins)
            metastore["calculated_metrics"]["gross_profit"]["canonical_sql"].format(**date_range),
            # 1: Party unpaid balance (cast + order by)
            metastore["calculated_metrics"]["party_unpaid_balance"]["canonical_sql"],
            # 2: Total purchases
            metastore["calculated_metrics"]["total_purchases"]["canonical_sql"].format(**date_range),
            # 3: Batch stock on hand (JOIN)
            metastore["calculated_metrics"]["batch_stock_on_hand"]["canonical_sql"],
            # 4: UDF string concat
            "SELECT CONCAT(name, ':', role) AS info FROM accounts WHERE id < 10",
            # 5: UDF date math
            "SELECT DATE_SUB('2026-11-02', INTERVAL 5 DAY) AS past_date, NOW() AS cur_ts",
            # 6: Simple aggregation
            "SELECT COUNT(*) AS total_accs FROM accounts",
            # 7: Total expenses
            metastore["calculated_metrics"]["total_expenses"]["canonical_sql"].format(**date_range),
        ]

        worker_count = 32
        iterations_per_worker = 5

        def _worker_task(thread_id: int):
            worker_results = []
            for i in range(iterations_per_worker):
                query_idx = (thread_id + i) % len(queries)
                sql = queries[query_idx]
                t0 = time.perf_counter()
                res = executor.execute_query(sql)
                elapsed = time.perf_counter() - t0
                assert res is not None
                assert res.row_count >= 0
                assert res.dialect == "sqlite"
                worker_results.append((query_idx, res.row_count, elapsed))
            return worker_results

        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = [pool.submit(_worker_task, tid) for tid in range(worker_count)]
            all_results = [f.result() for f in concurrent.futures.as_completed(futures)]

        assert len(all_results) == worker_count
        total_queries = sum(len(r) for r in all_results)
        assert total_queries == worker_count * iterations_per_worker

    def test_concurrent_engine_provisioning_race_condition(self):
        """Stress-test 20 threads simultaneously triggering provisioning on an uninitialized executor."""
        executor = DatabaseExecutor(mode="sqlite")
        thread_count = 25

        def _init_and_query(tid: int):
            res = executor.execute_query(f"SELECT COUNT(*) AS c, {tid} AS tid FROM accounts")
            return res.rows[0]["c"]

        with concurrent.futures.ThreadPoolExecutor(max_workers=thread_count) as pool:
            futures = [pool.submit(_init_and_query, i) for i in range(thread_count)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        assert len(results) == thread_count
        assert all(c == 55 for c in results)


# ============================================================================
# Section 3: Mid-flight Fallback Stress Test
# ============================================================================
class TestMidflightFallbackStressHarness:
    def test_single_query_midflight_mysql_connection_drop_falls_back_cleanly(self):
        """Simulate MySQL connection dropping during execution: must cleanly fall back to SQLite."""
        settings = Settings(db_url="mysql+pymysql://user:pass@127.0.0.1:3306/ledger")
        executor = DatabaseExecutor(settings=settings, mode="auto", auto_fallback=True)

        with patch.object(LiveMySQLEngine, "check_liveness", return_value=True):
            # First query succeeds on MySQL
            mock_mysql = MagicMock()
            mock_mysql.execute_query.side_effect = [
                ExecutionResult(
                    columns=["val"],
                    rows=[{"val": 1}],
                    execution_time_ms=5.0,
                    row_count=1,
                    dialect="mysql",
                    source_mode="live_mysql",
                ),
                # Second query experiences mid-flight network severing
                DatabaseConnectionError("Can't connect to MySQL server on '127.0.0.1' (61 Connection refused)"),
            ]
            with patch.object(executor, "_get_mysql_engine", return_value=mock_mysql):
                # 1. First execution on live MySQL
                res1 = executor.execute_query("SELECT 1 AS val")
                assert res1.source_mode == "live_mysql"
                assert res1.dialect == "mysql"

                # 2. Second execution drops mid-flight -> automatically falls back to SQLite
                res2 = executor.execute_query("SELECT COUNT(*) AS total FROM accounts")
                assert res2.source_mode == "sqlite_fallback"
                assert res2.dialect == "sqlite"
                assert res2.rows[0]["total"] == 55
                assert executor.active_mode == "sqlite_fallback"

                # 3. Third execution continues on SQLite fallback without re-attempting dead MySQL
                res3 = executor.execute_query("SELECT COUNT(*) AS total_vouchers FROM voucher")
                assert res3.source_mode == "sqlite_fallback"
                assert res3.rows[0]["total_vouchers"] == 5

    def test_concurrent_midflight_mysql_collapse(self):
        """25 concurrent threads querying live MySQL when mid-flight disconnection occurs."""
        settings = Settings(db_url="mysql+pymysql://user:pass@127.0.0.1:3306/ledger")
        executor = DatabaseExecutor(settings=settings, mode="auto", auto_fallback=True)

        call_count = 0
        call_lock = concurrent.futures.thread.threading.Lock()

        def mock_mysql_execute(sql, tenant_id="default"):
            nonlocal call_count
            with call_lock:
                call_count += 1
                current = call_count
            # First 5 calls succeed, remaining 20 encounter mid-flight connection loss
            if current <= 5:
                time.sleep(0.01)
                return ExecutionResult(
                    columns=["total"],
                    rows=[{"total": 55}],
                    execution_time_ms=5.0,
                    row_count=1,
                    dialect="mysql",
                    source_mode="live_mysql",
                )
            raise DatabaseConnectionError("MySQL server has gone away")

        with patch.object(LiveMySQLEngine, "check_liveness", return_value=True):
            mock_mysql = MagicMock()
            mock_mysql.execute_query.side_effect = mock_mysql_execute

            with patch.object(executor, "_get_mysql_engine", return_value=mock_mysql):
                def _run_concurrent(tid: int):
                    return executor.execute_query(f"SELECT COUNT(*) AS total FROM accounts -- thread {tid}")

                with concurrent.futures.ThreadPoolExecutor(max_workers=25) as pool:
                    futures = [pool.submit(_run_concurrent, i) for i in range(25)]
                    results = [f.result() for f in concurrent.futures.as_completed(futures)]

                assert len(results) == 25
                # All threads must successfully return total=55 regardless of whether they hit MySQL or SQLite
                for r in results:
                    assert r.rows[0]["total"] == 55
                    assert r.source_mode in ("live_mysql", "sqlite_fallback")

                # State must now be locked to sqlite_fallback
                assert executor.active_mode == "sqlite_fallback"

    def test_non_connection_error_does_not_trigger_fallback(self):
        """Schema/syntax errors on MySQL must NOT trigger SQLite fallback; they must fail-closed."""
        settings = Settings(db_url="mysql+pymysql://user:pass@127.0.0.1:3306/ledger")
        executor = DatabaseExecutor(settings=settings, mode="auto", auto_fallback=True)

        with patch.object(LiveMySQLEngine, "check_liveness", return_value=True):
            mock_mysql = MagicMock()
            # MySQL throws syntax error
            mock_mysql.execute_query.side_effect = DatabaseSyntaxError("You have an error in your SQL syntax near 'FRM'")

            with patch.object(executor, "_get_mysql_engine", return_value=mock_mysql):
                with pytest.raises(DatabaseSyntaxError):
                    executor.execute_query("SELECT FRM voucher")

                # Active mode must remain mysql because syntax error is not a connection failure
                assert executor.resolve_mode() == "mysql"


# ============================================================================
# Section 4: Adversarial Edge Cases & Stress Invariants
# ============================================================================
class TestAdversarialEngineInvariants:
    def test_timeout_interruption_leaves_connection_healthy_for_next_query(self, executor):
        """When a query times out, the engine must reset its progress handler and cleanly execute subsequent queries."""
        # 1. Force a timeout with a recursive infinite query
        infinite_sql = "WITH RECURSIVE cnt AS (SELECT 1 AS x UNION ALL SELECT x + 1 FROM cnt) SELECT * FROM cnt"
        with pytest.raises(StatementTimeoutError):
            # execute with 0.05s timeout
            executor._get_sqlite_engine().execute_query(infinite_sql, timeout_s=0.05)

        # 2. Immediately execute normal query: must succeed with no lingering interruption
        res = executor.execute_query("SELECT COUNT(*) AS total FROM accounts")
        assert res.row_count == 1
        assert res.rows[0]["total"] == 55

    def test_multi_instance_shared_memory_coexistence(self):
        """Verify two independent DatabaseExecutor instances sharing memory cache do not conflict."""
        exec1 = DatabaseExecutor(mode="sqlite")
        exec2 = DatabaseExecutor(mode="sqlite")

        res1 = exec1.execute_query("SELECT COUNT(*) AS c FROM voucher")
        res2 = exec2.execute_query("SELECT COUNT(*) AS c FROM voucher")
        assert res1.rows[0]["c"] == 5
        assert res2.rows[0]["c"] == 5

    def test_mysql_transpilation_backticks_and_reserved_group_keyword(self, executor):
        """Query using MySQL backticks on reserved keyword `group` in `accounts` table."""
        sql = "SELECT `group`, COUNT(*) AS cnt FROM `accounts` WHERE `group` IS NOT NULL GROUP BY `group` ORDER BY cnt DESC LIMIT 3"
        res = executor.execute_query(sql)
        assert res.row_count == 3
        assert "group" in res.columns
        assert res.rows[0]["cnt"] > 0

    def test_high_volume_concurrency_50_threads_500_queries(self, executor):
        """High-volume stress test: 50 threads x 10 queries = 500 queries."""
        thread_count = 50
        queries_per_thread = 10

        def _task(tid: int):
            for i in range(queries_per_thread):
                sql = f"SELECT id, name FROM accounts WHERE id = {((tid + i) % 55) + 1}"
                res = executor.execute_query(sql)
                assert res.row_count <= 1
            return True

        with concurrent.futures.ThreadPoolExecutor(max_workers=thread_count) as pool:
            futures = [pool.submit(_task, i) for i in range(thread_count)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        assert len(results) == thread_count
        assert all(r is True for r in results)

