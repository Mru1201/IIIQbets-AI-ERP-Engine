"""Adversarial stress-testing suite for Milestone 3: FastAPI Orchestrator & Self-Correction.

Empirical verification covering:
1. Rapid sequential and concurrent requests (thread safety, isolation, state leakage).
2. 3 consecutive syntax/schema/runtime errors (asserting structured status="error" without 500).
3. Malicious SQL injections (comments, UNION, stacked queries, DDL/DML, user query injection).
4. Boundary datasets (0 rows, 1 row, 500 rows, NULL values, unicode/special chars).
5. Comprehensive execution_metadata completeness invariant across all execution paths.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
from typing import Any, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient

from app.config import DEFAULT_SQL_DUMP_PATH, get_settings
from app.connectors.executor import (
    ColumnNotFoundError,
    DatabaseExecutionError,
    DatabaseExecutor,
    DatabaseSyntaxError,
    ExecutionResult,
    StatementTimeoutError,
    TableNotFoundError,
)
from app.inference.llm_client import LLMClient, LLMTimeoutError
from app.main import app
from app.metastore.loader import load_metastore
from app.schemas.report_plan import ReportPlanSchema
from app.security.guardrails import SQLSecurityViolation


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture(scope="module")
def dump_path() -> Path:
    return DEFAULT_SQL_DUMP_PATH


@pytest.fixture(scope="module")
def metastore() -> dict[str, Any]:
    return load_metastore()


@pytest.fixture
def sqlite_executor(dump_path: Path) -> DatabaseExecutor:
    executor = DatabaseExecutor(
        db_url=None,
        mode="sqlite",
        sql_dump_path=dump_path,
        auto_fallback=True,
    )
    executor.provision_sqlite()
    return executor


@pytest.fixture
def test_client() -> Generator[TestClient, None, None]:
    with TestClient(app) as client:
        yield client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    settings = get_settings()
    secret = getattr(settings, "jwt_secret", "erp-secret-key-change-in-prod")
    algorithm = getattr(settings, "jwt_algorithm", "HS256")
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "adversarial_tester",
        "tenant_id": "test_tenant_stress",
        "user_id": "adv_user_01",
        "role": "tester",
        "exp": now + timedelta(hours=1),
        "iat": now,
    }
    token = jwt.encode(payload, secret, algorithm=algorithm)
    return {"Authorization": f"Bearer {token}"}


def assert_metadata_completeness(metadata: dict[str, Any], expected_session_id: str | None = None) -> None:
    """Invariant: execution_metadata must always contain all 8 diagnostic fields."""
    required_fields = [
        "thought_process",
        "tables_used",
        "metrics_applied",
        "verified_sql",
        "retries_used",
        "retry_history",
        "latency_ms",
        "active_mode",
    ]
    for field in required_fields:
        assert field in metadata, f"Missing required execution_metadata field: {field}"

    assert isinstance(metadata["thought_process"], str)
    assert isinstance(metadata["tables_used"], list)
    assert isinstance(metadata["metrics_applied"], list)
    assert metadata["verified_sql"] is None or isinstance(metadata["verified_sql"], str)
    assert isinstance(metadata["retries_used"], int) and metadata["retries_used"] >= 0
    assert isinstance(metadata["retry_history"], list)
    assert isinstance(metadata["latency_ms"], (int, float)) and metadata["latency_ms"] > 0
    assert metadata["active_mode"] in ("sqlite", "sqlite_fallback", "live_mysql")

    if expected_session_id is not None:
        assert metadata.get("session_id") == expected_session_id


# =====================================================================
# 1. Rapid Sequential & Concurrent Queries
# =====================================================================

class TestRapidAndConcurrentQueries:
    def test_rapid_sequential_queries_no_state_leak(
        self, test_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Execute 50 rapid sequential requests; verify thread safety and session isolation."""
        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            for i in range(50):
                session_id = f"seq-sess-{i:03d}"
                mock_llm.return_value = ReportPlanSchema(
                    thought_process=f"Reasoning for query {i}",
                    tables_used=["voucher"],
                    metrics_applied=["total_sales_revenue"],
                    sql_query=f"SELECT {i} AS query_idx, COALESCE(SUM(TotalAmount), 0) AS total_revenue FROM voucher",
                    suggested_visualization="kpi_card",
                )

                resp = test_client.post(
                    "/api/v1/reports/query",
                    json={
                        "user_query": f"Sales query {i}",
                        "session_id": session_id,
                    },
                    headers=auth_headers,
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "success"
                assert data["session_id"] == session_id
                assert data["data"][0]["query_idx"] == i
                assert_metadata_completeness(data["execution_metadata"], expected_session_id=session_id)
                assert data["execution_metadata"]["thought_process"] == f"Reasoning for query {i}"

    def test_concurrent_multi_threaded_queries(
        self, test_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Execute 20 simultaneous concurrent requests across multiple threads."""
        def _send_query(idx: int) -> dict[str, Any]:
            sess_id = f"concurrent-{idx:02d}"
            resp = test_client.post(
                "/api/v1/reports/query",
                json={
                    "user_query": f"Concurrent test {idx}",
                    "session_id": sess_id,
                },
                headers=auth_headers,
            )
            return {"status_code": resp.status_code, "body": resp.json(), "session_id": sess_id, "idx": idx}

        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            async def _fake_plan(user_query: str, **kwargs: Any) -> ReportPlanSchema:
                return ReportPlanSchema(
                    thought_process=f"Plan for: {user_query}",
                    tables_used=["voucher"],
                    metrics_applied=["total_sales_revenue"],
                    sql_query="SELECT COUNT(*) AS cnt, COALESCE(SUM(TotalAmount), 0) AS total_revenue FROM voucher",
                    suggested_visualization="kpi_card",
                )

            mock_llm.side_effect = _fake_plan

            with ThreadPoolExecutor(max_workers=5) as pool:
                futures = [pool.submit(_send_query, i) for i in range(20)]
                results = [f.result() for f in futures]

        assert len(results) == 20
        for res in results:
            assert res["status_code"] == 200
            body = res["body"]
            assert body["status"] == "success"
            assert body["session_id"] == res["session_id"]
            assert_metadata_completeness(body["execution_metadata"], expected_session_id=res["session_id"])
            assert body["execution_metadata"]["thought_process"] == f"Plan for: Concurrent test {res['idx']}"

    def test_concurrent_mixed_success_and_error_queries(
        self, test_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Concurrent requests mixing successes, refusals, and errors don't contaminate state."""
        def _run_task(task_type: str, idx: int) -> dict[str, Any]:
            sess_id = f"mixed-{task_type}-{idx}"
            resp = test_client.post(
                "/api/v1/reports/query",
                json={"user_query": f"Task {task_type} {idx}", "session_id": sess_id},
                headers=auth_headers,
            )
            return {"task_type": task_type, "status_code": resp.status_code, "body": resp.json(), "sess_id": sess_id}

        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            async def _mixed_plan(user_query: str, **kwargs: Any) -> ReportPlanSchema:
                if "refuse" in user_query:
                    return ReportPlanSchema(
                        thought_process="Refusal plan",
                        is_refusal=True,
                        error="Direct refusal",
                        sql_query=None,
                    )
                elif "fail" in user_query:
                    return ReportPlanSchema(
                        thought_process="Failing SQL plan",
                        tables_used=["voucher"],
                        sql_query="SELECT * FROOOM voucher",  # Syntax error
                    )
                return ReportPlanSchema(
                    thought_process="Successful plan",
                    tables_used=["voucher"],
                    sql_query="SELECT 1 AS val",
                    suggested_visualization="kpi_card",
                )

            mock_llm.side_effect = _mixed_plan

            tasks = [("succ", 0), ("refuse", 1), ("fail", 2), ("succ", 3), ("fail", 4), ("refuse", 5)]
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(_run_task, t, i) for t, i in tasks]
                results = [f.result() for f in futures]

        for r in results:
            assert r["status_code"] == 200
            body = r["body"]
            assert body["session_id"] == r["sess_id"]
            assert_metadata_completeness(body["execution_metadata"], expected_session_id=r["sess_id"])
            if r["task_type"] == "succ":
                assert body["status"] == "success"
                assert body["error"] is None
            elif r["task_type"] == "refuse":
                assert body["status"] == "error"
                assert "refusal" in (body["error"] or "").lower() or "direct refusal" in (body["error"] or "").lower()
            elif r["task_type"] == "fail":
                assert body["status"] == "error"
                assert len(body["execution_metadata"]["retry_history"]) == 3


# =====================================================================
# 2. Self-Correction State Machine: 3 Consecutive Errors (No 500)
# =====================================================================

class TestSelfCorrectionEdgeCases:
    def test_three_consecutive_syntax_errors_returns_structured_error(
        self, test_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """When LLM returns 3 consecutive syntax errors, assert structured status='error', retries=3, NO 500."""
        syntax_errors = [
            "SELECT * FROOOM voucher",
            "SELECT ,,, FROM voucher",
            "SELECT * FROM (voucher WHERE)",
        ]
        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = [
                ReportPlanSchema(thought_process=f"Attempt {i+1} syntax error", sql_query=q)
                for i, q in enumerate(syntax_errors)
            ]

            resp = test_client.post(
                "/api/v1/reports/query",
                json={"user_query": "Give me sales data", "session_id": "syntax-3-err"},
                headers=auth_headers,
            )
            assert resp.status_code == 200, f"Expected 200 OK, got {resp.status_code}: {resp.text}"
            data = resp.json()

            assert data["status"] == "error"
            assert data["session_id"] == "syntax-3-err"
            assert data["error"] is not None
            assert any(term in data["error"].lower() for term in ("syntax", "guardrail", "ast", "parse"))

            meta = data["execution_metadata"]
            assert_metadata_completeness(meta, expected_session_id="syntax-3-err")
            assert meta["retries_used"] == 3
            assert len(meta["retry_history"]) == 3

            for i, item in enumerate(meta["retry_history"]):
                assert item["attempt"] == i + 1
                assert item["error_type"] == "SYNTAX_ERROR"
                assert item["failed_sql"] is not None
                assert "voucher" in item["failed_sql"].lower()
                assert item["correction_hint"] is not None
                assert isinstance(item["timestamp"], (int, float))

    def test_three_consecutive_schema_errors(
        self, test_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """When LLM produces unknown tables/columns on all 3 attempts, returns structured error without 500."""
        schema_errs = [
            "SELECT * FROM non_existent_payroll_table",
            "SELECT non_existent_column FROM voucher",
            "SELECT fake_id FROM voucherdetails",
        ]
        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = [
                ReportPlanSchema(thought_process=f"Attempt {i+1} schema error", sql_query=q)
                for i, q in enumerate(schema_errs)
            ]

            resp = test_client.post(
                "/api/v1/reports/query",
                json={"user_query": "Give me payroll records"},
                headers=auth_headers,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "error"
            meta = data["execution_metadata"]
            assert meta["retries_used"] == 3
            assert len(meta["retry_history"]) == 3
            for item in meta["retry_history"]:
                assert item["error_type"] == "SCHEMA_ERROR"
                assert item["error_code"] in ("UNKNOWN_TABLE", "UNKNOWN_COLUMN")

    def test_cascading_multi_type_errors(
        self, test_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Verify cascade across different error classes: AST Schema -> DB Column -> DB Timeout."""
        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            # Attempt 1: AST Schema error (unknown table)
            plan1 = ReportPlanSchema(
                thought_process="Attempt 1: bad table",
                sql_query="SELECT * FROM unknown_table_xyz",
            )
            # Attempt 2: AST passes with valid voucher columns, but DB raises ColumnNotFoundError
            plan2 = ReportPlanSchema(
                thought_process="Attempt 2: DB column missing",
                sql_query="SELECT VoucherID, TotalAmount FROM voucher",
            )
            # Attempt 3: AST passes with valid voucher columns, but DB raises StatementTimeoutError
            plan3 = ReportPlanSchema(
                thought_process="Attempt 3: DB timeout",
                sql_query="SELECT VoucherID FROM voucher",
            )
            mock_llm.side_effect = [plan1, plan2, plan3]

            with patch.object(DatabaseExecutor, "execute_query") as mock_exec:
                mock_exec.side_effect = [
                    ColumnNotFoundError("Column 'xyz' not found in table voucher"),
                    StatementTimeoutError("Query timed out after 5000ms"),
                ]

                resp = test_client.post(
                    "/api/v1/reports/query",
                    json={"user_query": "Stress cascade"},
                    headers=auth_headers,
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "error"
                meta = data["execution_metadata"]
                assert meta["retries_used"] == 3
                assert len(meta["retry_history"]) == 3

                # Attempt 1 failed at AST validation
                assert meta["retry_history"][0]["attempt"] == 1
                assert meta["retry_history"][0]["error_type"] == "SCHEMA_ERROR"

                # Attempt 2 failed at DB execution (ColumnNotFound)
                assert meta["retry_history"][1]["attempt"] == 2
                assert meta["retry_history"][1]["error_type"] == "ColumnNotFoundError"

                # Attempt 3 failed at DB execution (Timeout)
                assert meta["retry_history"][2]["attempt"] == 3
                assert meta["retry_history"][2]["error_type"] == "TIMEOUT"

    def test_llm_inference_exceptions_exhaustion(
        self, test_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """When LLM inference throws unexpected exceptions across all 3 attempts, returns 200 with structured error."""
        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = RuntimeError("GPU Out of Memory on vLLM server")

            resp = test_client.post(
                "/api/v1/reports/query",
                json={"user_query": "Trigger LLM crash"},
                headers=auth_headers,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "error"
            assert "GPU Out of Memory" in data["error"]
            assert data["execution_metadata"]["retries_used"] == 3
            assert len(data["execution_metadata"]["retry_history"]) == 3
            for item in data["execution_metadata"]["retry_history"]:
                assert item["error_type"] == "LLM_INFERENCE_ERROR"


# =====================================================================
# 3. Malicious SQL Injections & Red-Team Attacks
# =====================================================================

class TestMaliciousSQLInjectionAndAdversarialInputs:
    @pytest.mark.parametrize(
        "malicious_sql, expected_error_type",
        [
            # Stacked query
            ("SELECT * FROM voucher; DROP TABLE voucher;", "GUARDRAIL_VIOLATION"),
            ("SELECT * FROM voucher; DELETE FROM voucherdetails;", "GUARDRAIL_VIOLATION"),
            ("SELECT * FROM voucher; UPDATE voucher SET totalamount = 0;", "GUARDRAIL_VIOLATION"),
            # DDL commands
            ("DROP TABLE voucher", "GUARDRAIL_VIOLATION"),
            ("ALTER TABLE voucher ADD COLUMN backdoor TEXT", "GUARDRAIL_VIOLATION"),
            ("TRUNCATE TABLE voucher", "GUARDRAIL_VIOLATION"),
            # DML mutations
            ("DELETE FROM voucher WHERE 1=1", "GUARDRAIL_VIOLATION"),
            ("UPDATE voucher SET totalamount = 999999 WHERE VoucherID = 1", "GUARDRAIL_VIOLATION"),
            ("INSERT INTO voucher (VchNo) VALUES ('HACK')", "GUARDRAIL_VIOLATION"),
            # Executable MySQL comments
            ("SELECT * FROM voucher /*!50000 WHERE 1=1 */", "GUARDRAIL_VIOLATION"),
            ("SELECT * FROM voucher /*!32302 1/0, */", "GUARDRAIL_VIOLATION"),
            # Union to unauthorized / unknown table
            ("SELECT VoucherID, TotalAmount FROM voucher UNION SELECT user_id, password FROM user_credentials", "SCHEMA_ERROR"),
            # Union to restricted table (gsp_credentials)
            ("SELECT VoucherID, TotalAmount FROM voucher UNION SELECT 1, 2 FROM gsp_credentials", "GUARDRAIL_VIOLATION"),
            # Excluded column exfiltration (accounts.password)
            ("SELECT VoucherID, TotalAmount FROM voucher UNION SELECT 1, password FROM accounts", "GUARDRAIL_VIOLATION"),
        ],
    )
    def test_malicious_llm_sql_blocked_without_500(
        self,
        test_client: TestClient,
        auth_headers: dict[str, str],
        malicious_sql: str,
        expected_error_type: str,
    ) -> None:
        """Ensure malicious queries produced by LLM are strictly blocked and never executed against DB."""
        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = ReportPlanSchema(
                thought_process="Malicious query attempt",
                tables_used=["voucher"],
                sql_query=malicious_sql,
            )

            resp = test_client.post(
                "/api/v1/reports/query",
                json={"user_query": "Harmful reporting request"},
                headers=auth_headers,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "error"
            assert data["error"] is not None

            # Verify that retry history captured the guardrail violation
            retry_hist = data["execution_metadata"]["retry_history"]
            assert len(retry_hist) == 3  # Tried 3 times, all blocked
            for attempt_log in retry_hist:
                assert attempt_log["error_type"] in ("GUARDRAIL_VIOLATION", "SCHEMA_ERROR")
                assert attempt_log["failed_sql"] == malicious_sql

    @pytest.mark.parametrize(
        "adversarial_prompt",
        [
            "' OR '1'='1",
            "1; DROP TABLE voucher; --",
            "admin' --",
            "UNION SELECT * FROM sqlite_master --",
            "<script>alert('xss')</script>",
            "'; EXEC xp_cmdshell('dir'); --",
            "{\"malicious\": \"json_injection\"}",
            "' OR SLEEP(5) --",
        ],
    )
    def test_sql_injection_payloads_in_user_query_handled_safely(
        self,
        test_client: TestClient,
        auth_headers: dict[str, str],
        adversarial_prompt: str,
    ) -> None:
        """Ensure hostile user input prompts do not crash the service or bypass security."""
        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = ReportPlanSchema(
                thought_process="Sanitized interpretation",
                tables_used=["voucher"],
                metrics_applied=["total_sales_revenue"],
                sql_query="SELECT COALESCE(SUM(TotalAmount), 0) AS total_revenue FROM voucher",
                suggested_visualization="kpi_card",
            )

            resp = test_client.post(
                "/api/v1/reports/query",
                json={"user_query": adversarial_prompt},
                headers=auth_headers,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "success"
            assert_metadata_completeness(data["execution_metadata"])


# =====================================================================
# 4. Boundary Datasets & Extreme Shapes (0, 1, 500 rows, NULLs, Unicode)
# =====================================================================

class TestDatasetBoundariesAndExtremeShapes:
    def test_zero_rows_empty_result(
        self, test_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Query returning 0 rows returns status='success', data=[], ui_schema='table', kpis=[]."""
        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = ReportPlanSchema(
                thought_process="Query yielding no results",
                tables_used=["voucher"],
                sql_query="SELECT VoucherID, VchNo, TotalAmount FROM voucher WHERE VoucherID = -999999",
                suggested_visualization="table",
            )

            resp = test_client.post(
                "/api/v1/reports/query",
                json={"user_query": "Non-existent records"},
                headers=auth_headers,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "success"
            assert data["data"] == []
            assert data["ui_schema"]["suggested_visualization"] == "table"
            assert data["ui_schema"]["kpis"] == []
            assert len(data["ui_schema"]["columns"]) >= 1

    def test_single_row_scalar_kpi(
        self, test_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Single summary row derives kpi_grid with formatted currency and positive trend."""
        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = ReportPlanSchema(
                thought_process="Single row scalar KPI calculation",
                tables_used=["voucher"],
                metrics_applied=["total_sales_revenue"],
                sql_query="SELECT COALESCE(SUM(TotalAmount), 0) AS total_sales_revenue FROM voucher WHERE TransactionType = 'Sales'",
                suggested_visualization="kpi_card",
            )

            resp = test_client.post(
                "/api/v1/reports/query",
                json={"user_query": "Total sales revenue"},
                headers=auth_headers,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "success"
            assert len(data["data"]) == 1
            assert data["data"][0]["total_sales_revenue"] == 10368.0
            assert data["ui_schema"]["suggested_visualization"] == "kpi_grid"
            assert len(data["ui_schema"]["kpis"]) >= 1
            card = data["ui_schema"]["kpis"][0]
            assert "10,368.00 INR" in str(card["value"])
            assert card["trend"] == "positive"

    def test_large_dataset_500_rows(
        self, test_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Large dataset of 500 rows is processed efficiently without latency spikes or schema errors."""
        large_rows = [
            {
                "VoucherID": i,
                "VchNo": f"INV-{i:04d}",
                "TotalAmount": float(i * 10.5),
                "Date": "2026-03-15",
                "TransactionType": "Sales",
            }
            for i in range(1, 501)
        ]
        columns = ["VoucherID", "VchNo", "TotalAmount", "Date", "TransactionType"]

        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = ReportPlanSchema(
                thought_process="Large tabular query",
                tables_used=["voucher"],
                sql_query="SELECT VoucherID, VchNo, TotalAmount, Date, TransactionType FROM voucher LIMIT 500",
                suggested_visualization="table",
            )

            with patch.object(DatabaseExecutor, "execute_query") as mock_exec:
                mock_exec.return_value = ExecutionResult(
                    columns=columns,
                    rows=large_rows,
                    execution_time_ms=12.4,
                    row_count=500,
                    dialect="sqlite",
                    source_mode="sqlite_fallback",
                )

                resp = test_client.post(
                    "/api/v1/reports/query",
                    json={"user_query": "Fetch 500 records"},
                    headers=auth_headers,
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "success"
                assert len(data["data"]) == 500
                assert data["ui_schema"]["suggested_visualization"] == "table"
                kpis = {k["label"]: k["value"] for k in data["ui_schema"]["kpis"]}
                assert kpis.get("Total Records") == 500
                assert "Total Total Amount" in kpis
                assert "Avg Total Amount" in kpis
                assert data["execution_metadata"]["latency_ms"] < 1000.0

    def test_null_and_missing_values_handling(
        self, test_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Result rows containing NULL values across multiple types are safely handled without crash."""
        null_rows = [
            {"VoucherID": 1, "TotalAmount": None, "Date": None, "PartyName": None},
            {"VoucherID": 2, "TotalAmount": 150.0, "Date": "2026-01-01", "PartyName": "Valid Party"},
            {"VoucherID": 3, "TotalAmount": None, "Date": None, "PartyName": None},
        ]
        columns = ["VoucherID", "TotalAmount", "Date", "PartyName"]

        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = ReportPlanSchema(
                thought_process="Query returning null fields",
                tables_used=["voucher"],
                sql_query="SELECT VoucherID, TotalAmount, Date, PartyName FROM voucher LIMIT 3",
                suggested_visualization="table",
            )

            with patch.object(DatabaseExecutor, "execute_query") as mock_exec:
                mock_exec.return_value = ExecutionResult(
                    columns=columns,
                    rows=null_rows,
                    execution_time_ms=5.0,
                    row_count=3,
                    dialect="sqlite",
                    source_mode="sqlite_fallback",
                )

                resp = test_client.post(
                    "/api/v1/reports/query",
                    json={"user_query": "Records with null values"},
                    headers=auth_headers,
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "success"
                assert len(data["data"]) == 3
                col_types = {c["key"]: c["type"] for c in data["ui_schema"]["columns"]}
                assert col_types["TotalAmount"] == "currency"
                assert col_types["Date"] == "date"
                assert col_types["PartyName"] == "string"

    def test_unicode_emojis_and_html_special_chars(
        self, test_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Ensure Unicode characters, currency symbols, emojis, and HTML entities serialize cleanly."""
        special_rows = [
            {
                "VoucherID": 1,
                "PartyName": "Agro Exports 🇮🇳 🌾 <script>alert('xss')</script> & 'quoted' \\ escaped",
                "TotalAmount": 250000.75,
            }
        ]
        columns = ["VoucherID", "PartyName", "TotalAmount"]

        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = ReportPlanSchema(
                thought_process="Unicode and emoji query 💰",
                tables_used=["voucher"],
                sql_query="SELECT VoucherID, PartyName, TotalAmount FROM voucher LIMIT 1",
                suggested_visualization="kpi_card",
            )

            with patch.object(DatabaseExecutor, "execute_query") as mock_exec:
                mock_exec.return_value = ExecutionResult(
                    columns=columns,
                    rows=special_rows,
                    execution_time_ms=3.0,
                    row_count=1,
                    dialect="sqlite",
                    source_mode="sqlite_fallback",
                )

                resp = test_client.post(
                    "/api/v1/reports/query",
                    json={"user_query": "Query with unicode: कृषि & 🌾"},
                    headers=auth_headers,
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "success"
                assert "Agro Exports 🇮🇳 🌾" in data["data"][0]["PartyName"]
                assert "<script>alert('xss')</script>" in data["data"][0]["PartyName"]
