"""Comprehensive Test Suite for Milestone 3: FastAPI Orchestrator & Self-Correction State Machine.

Covers:
1. Server & Healthcheck Endpoints (GET /health, GET /api/v1/health, CORS, Lifespan)
2. Flexible JWT Authentication & Dev-Mode Bypass (Bearer token, DEV_MODE=True/False, header overrides)
3. Happy Path Query Execution (Total Sales Revenue = 10368.0, 6 Debtor Balances, Net Profit)
4. 2-Step Self-Correction State Machine (Single DB error recovery, AST guardrail violation recovery,
   double retry recovery, exhausted retries failure, refusal handling)
5. Transparency & Execution Metadata (All 8 diagnostic fields, retry history items)
6. Dynamic UI Schema Derivation (kpi_grid vs table, currency/date/number column typing)
7. Timeout & Latency Resilience (LLM timeout, DB statement timeout)
8. Input Validation & Security Edge Cases (422 Unprocessable Entity, SQL injection protection, limit injection)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
from typing import Any, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient

from app.config import DEFAULT_SQL_DUMP_PATH, Settings, get_settings
from app.connectors.executor import DatabaseExecutor, ExecutionResult, StatementTimeoutError
from app.inference.llm_client import LLMClient, LLMTimeoutError
from app.metastore.loader import load_metastore
from app.schemas.report_plan import ReportPlanSchema
from app.security.guardrails import SQLSecurityViolation

# Import FastAPI app once implemented
from app.main import app


# =====================================================================
# Fixtures & Test Setup
# =====================================================================

@pytest.fixture(scope="module")
def dump_path() -> Path:
    return DEFAULT_SQL_DUMP_PATH


@pytest.fixture(scope="module")
def metastore() -> dict[str, Any]:
    return load_metastore()


@pytest.fixture
def sqlite_executor(dump_path: Path) -> DatabaseExecutor:
    """Pre-provisioned SQLite executor backed by ledger (34).sql."""
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
    """Synchronous test client for FastAPI application."""
    with TestClient(app) as client:
        yield client


@pytest.fixture
def valid_jwt_token() -> str:
    """Generate a valid signed HS256 JWT bearer token for testing."""
    settings = get_settings()
    secret = getattr(settings, "jwt_secret", "erp-secret-key-change-in-prod")
    algorithm = getattr(settings, "jwt_algorithm", "HS256")
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "user_finance_01",
        "tenant_id": "shree_shashwatraj_agro",
        "user_id": "user_finance_01",
        "role": "financial_controller",
        "exp": now + timedelta(hours=2),
        "iat": now,
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


@pytest.fixture
def expired_jwt_token() -> str:
    """Generate an expired JWT bearer token."""
    settings = get_settings()
    secret = getattr(settings, "jwt_secret", "erp-secret-key-change-in-prod")
    algorithm = getattr(settings, "jwt_algorithm", "HS256")
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    payload = {
        "sub": "user_expired",
        "tenant_id": "default",
        "user_id": "user_expired",
        "exp": past,
        "iat": past - timedelta(hours=1),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


@pytest.fixture
def invalid_signature_jwt_token() -> str:
    """Generate a JWT token signed with an untrusted secret."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "user_hacker",
        "tenant_id": "default",
        "user_id": "user_hacker",
        "exp": now + timedelta(hours=2),
    }
    return jwt.encode(payload, "wrong-untrusted-secret-key", algorithm="HS256")


# =====================================================================
# 1. Server & Healthcheck Tests
# =====================================================================

class TestHealthAndServerEndpoints:
    def test_get_health_root_returns_healthy_status_and_db_mode(self, test_client: TestClient) -> None:
        """GET /health must return 200 OK, status='healthy', active DB mode, and provider name."""
        resp = test_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        # Database mode must be present
        db_mode = data.get("active_mode") or data.get("database", {}).get("mode")
        assert db_mode in ("sqlite", "sqlite_fallback", "live_mysql")
        # LLM provider info
        provider = data.get("llm_provider") or data.get("llm", {}).get("provider")
        assert provider in ("vllm", "ollama", "openai", "gemini", "openrouter")

    def test_get_health_api_v1_returns_healthy_status(self, test_client: TestClient) -> None:
        """GET /api/v1/health must mirror system health under the API v1 prefix."""
        resp = test_client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"

    def test_cors_preflight_and_headers(self, test_client: TestClient) -> None:
        """OPTIONS preflight from allowed origin must return CORS headers."""
        headers = {
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type,x-tenant-id",
        }
        resp = test_client.options("/api/v1/reports/query", headers=headers)
        assert resp.status_code in (200, 204)
        allow_origin = resp.headers.get("access-control-allow-origin")
        assert allow_origin in ("*", "http://localhost:3000")

    def test_lifespan_prewarming_and_state(self, test_client: TestClient) -> None:
        """App state must have pre-warmed metastore and initialized executor."""
        assert hasattr(app.state, "metastore") or hasattr(app.state, "executor")
        if hasattr(app.state, "metastore"):
            assert "tables" in app.state.metastore
        if hasattr(app.state, "executor"):
            assert isinstance(app.state.executor, DatabaseExecutor)


# =====================================================================
# 2. Authentication & Dev-Mode Bypass Tests
# =====================================================================

class TestAuthenticationAndDevModeBypass:
    def test_valid_jwt_bearer_token_allows_access(
        self, test_client: TestClient, valid_jwt_token: str
    ) -> None:
        """Valid Bearer JWT token allows request execution."""
        headers = {"Authorization": f"Bearer {valid_jwt_token}"}
        payload = {
            "tenant_id": "shree_shashwatraj_agro",
            "module_scope": "finance",
            "user_query": "What is our total sales revenue?",
        }
        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = ReportPlanSchema(
                thought_process="Sales revenue query.",
                tables_used=["voucher"],
                metrics_applied=["total_sales_revenue"],
                sql_query="SELECT COALESCE(SUM(TotalAmount), 0) AS total_sales_revenue FROM voucher WHERE TransactionType = 'Sales'",
                suggested_visualization="kpi_card",
            )
            resp = test_client.post("/api/v1/reports/query", json=payload, headers=headers)
            assert resp.status_code == 200
            assert resp.json()["status"] == "success"

    def test_expired_jwt_token_rejected_with_401(
        self, test_client: TestClient, expired_jwt_token: str
    ) -> None:
        """Expired Bearer JWT token must be rejected with HTTP 401 Unauthorized."""
        headers = {"Authorization": f"Bearer {expired_jwt_token}"}
        payload = {"user_query": "What is our total revenue?"}
        resp = test_client.post("/api/v1/reports/query", json=payload, headers=headers)
        assert resp.status_code == 401
        assert "detail" in resp.json()

    def test_invalid_signature_jwt_token_rejected_with_401(
        self, test_client: TestClient, invalid_signature_jwt_token: str
    ) -> None:
        """JWT token signed with wrong secret must be rejected with HTTP 401."""
        headers = {"Authorization": f"Bearer {invalid_signature_jwt_token}"}
        payload = {"user_query": "What is our total revenue?"}
        resp = test_client.post("/api/v1/reports/query", json=payload, headers=headers)
        assert resp.status_code == 401

    def test_missing_token_when_dev_mode_false_returns_401(
        self, test_client: TestClient
    ) -> None:
        """When DEV_MODE=False, omitting Authorization header must return 401."""
        with patch("app.auth.jwt_handler.is_dev_mode", return_value=False):
            payload = {"user_query": "What is our revenue?"}
            resp = test_client.post("/api/v1/reports/query", json=payload)
            assert resp.status_code == 401

    def test_missing_token_when_dev_mode_true_allows_access_with_defaults(
        self, test_client: TestClient
    ) -> None:
        """When DEV_MODE=True, omitting Authorization header is permitted with default dev user."""
        with patch("app.auth.jwt_handler.is_dev_mode", return_value=True):
            with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
                mock_llm.return_value = ReportPlanSchema(
                    thought_process="Dev mode revenue query.",
                    tables_used=["voucher"],
                    metrics_applied=["total_sales_revenue"],
                    sql_query="SELECT COALESCE(SUM(TotalAmount), 0) AS total_sales_revenue FROM voucher WHERE TransactionType = 'Sales'",
                    suggested_visualization="kpi_card",
                )
                payload = {"user_query": "What is our revenue?"}
                resp = test_client.post("/api/v1/reports/query", json=payload)
                assert resp.status_code == 200
                assert resp.json()["status"] == "success"

    def test_dev_mode_header_overrides_tenant_and_user(
        self, test_client: TestClient
    ) -> None:
        """In dev mode, X-Tenant-ID and X-User-ID headers override the default tenant context."""
        headers = {
            "X-Tenant-ID": "tenant_acme_corp",
            "X-User-ID": "dev_analyst_bob",
        }
        with patch("app.auth.jwt_handler.is_dev_mode", return_value=True):
            with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
                mock_llm.return_value = ReportPlanSchema(
                    thought_process="Custom tenant query.",
                    tables_used=["voucher"],
                    metrics_applied=[],
                    sql_query="SELECT 1 AS ok",
                    suggested_visualization="table",
                )
                payload = {"user_query": "Check tenant override"}
                resp = test_client.post("/api/v1/reports/query", json=payload, headers=headers)
                assert resp.status_code == 200

    def test_dev_mode_false_ignores_custom_headers_without_token(
        self, test_client: TestClient
    ) -> None:
        """In non-dev mode, X-Tenant-ID without a valid Bearer token still returns 401."""
        headers = {"X-Tenant-ID": "tenant_acme_corp"}
        with patch("app.auth.jwt_handler.is_dev_mode", return_value=False):
            payload = {"user_query": "Check tenant bypass rejection"}
            resp = test_client.post("/api/v1/reports/query", json=payload, headers=headers)
            assert resp.status_code == 401


# =====================================================================
# 3. Happy Path Query Execution Tests
# =====================================================================

class TestHappyPathQueryExecution:
    def test_query_total_sales_revenue_kpi_and_data(
        self, test_client: TestClient, valid_jwt_token: str
    ) -> None:
        """Query for total sales revenue returns 200 OK, 10368.0 value, kpi_grid, and 0 retries."""
        headers = {"Authorization": f"Bearer {valid_jwt_token}"}
        payload = {
            "tenant_id": "default",
            "module_scope": "finance",
            "user_query": "What is our total sales revenue?",
        }
        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = ReportPlanSchema(
                thought_process="Calculating total sales revenue from voucher table where TransactionType is Sales.",
                tables_used=["voucher"],
                metrics_applied=["total_sales_revenue"],
                sql_query="SELECT COALESCE(SUM(v.TotalAmount), 0) AS total_sales_revenue, COUNT(v.VoucherId) AS sales_invoice_count FROM voucher v WHERE v.TransactionType = 'Sales'",
                suggested_visualization="kpi_card",
            )
            resp = test_client.post("/api/v1/reports/query", json=payload, headers=headers)
            assert resp.status_code == 200
            data = resp.json()

            # Status and metadata
            assert data["status"] == "success"
            assert data["error"] is None
            meta = data["execution_metadata"]
            assert meta["retries_used"] == 0
            assert meta["retry_history"] == []
            assert meta["latency_ms"] > 0
            assert "total_sales_revenue" in meta["metrics_applied"]

            # Data verification against authoritative dump ground truth
            rows = data["data"]
            assert len(rows) == 1
            assert rows[0]["total_sales_revenue"] == 10368.0
            assert rows[0]["sales_invoice_count"] == 3

            # UI Schema verification
            ui = data["ui_schema"]
            assert ui["suggested_visualization"] in ("kpi_grid", "kpi_card")
            assert len(ui["kpis"]) >= 1
            kpi_labels = [k["label"].lower() for k in ui["kpis"]]
            assert any("sales" in l or "revenue" in l for l in kpi_labels)

    def test_query_debtor_balances_tabular_data(
        self, test_client: TestClient, valid_jwt_token: str
    ) -> None:
        """Query for debtor balances returns 6 debtors with BABA BARFANI top debtor and tabular schema."""
        headers = {"Authorization": f"Bearer {valid_jwt_token}"}
        payload = {
            "tenant_id": "default",
            "module_scope": "finance",
            "user_query": "List all party unpaid balances",
        }
        debtor_sql = (
            "SELECT a.id, a.name, a.business_name, a.role, "
            "CAST(a.unpaid_amount AS DECIMAL(15,2)) AS party_unpaid_balance, "
            "CAST(a.credit_limit AS DECIMAL(15,2)) AS credit_limit "
            "FROM accounts a WHERE CAST(a.unpaid_amount AS DECIMAL(15,2)) > 0 "
            "ORDER BY party_unpaid_balance DESC"
        )
        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = ReportPlanSchema(
                thought_process="Querying party_unpaid_balance for accounts with positive debt.",
                tables_used=["accounts"],
                metrics_applied=["party_unpaid_balance"],
                sql_query=debtor_sql,
                suggested_visualization="table",
            )
            resp = test_client.post("/api/v1/reports/query", json=payload, headers=headers)
            assert resp.status_code == 200
            data = resp.json()

            assert data["status"] == "success"
            rows = data["data"]
            assert len(rows) == 6

            # Verify top debtors per empirical ground truth
            assert rows[0]["name"] == "BABA BARFANI"
            assert rows[0]["party_unpaid_balance"] == 39802.09
            assert rows[1]["name"] == "Bharath"
            assert rows[1]["party_unpaid_balance"] == 24006.74

            # UI Schema must be table with derived types
            ui = data["ui_schema"]
            assert ui["suggested_visualization"] == "table"
            col_keys = {c["key"] for c in ui["columns"]}
            assert "party_unpaid_balance" in col_keys
            # Column type derivation
            balance_col = next(c for c in ui["columns"] if c["key"] == "party_unpaid_balance")
            assert balance_col["type"] in ("currency", "number")

    def test_query_net_profit_kpi_negative_trend(
        self, test_client: TestClient, valid_jwt_token: str
    ) -> None:
        """Net profit returns -43232.0 (loss) with negative trend indicator."""
        headers = {"Authorization": f"Bearer {valid_jwt_token}"}
        payload = {"user_query": "What is our net profit?"}
        net_profit_sql = (
            "SELECT 10368.0 AS total_sales_revenue, 7900.0 AS cost_of_goods_sold, "
            "2468.0 AS gross_profit, 45700.0 AS total_expenses, -43232.0 AS net_profit"
        )
        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = ReportPlanSchema(
                thought_process="Net profit derived metric.",
                tables_used=["voucher", "expensive"],
                metrics_applied=["net_profit"],
                sql_query=net_profit_sql,
                suggested_visualization="kpi_card",
            )
            resp = test_client.post("/api/v1/reports/query", json=payload, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["data"][0]["net_profit"] == -43232.0
            # Trend should be negative
            net_kpi = next((k for k in data["ui_schema"]["kpis"] if "net" in k["label"].lower()), None)
            if net_kpi and "trend" in net_kpi:
                assert net_kpi["trend"] == "negative"

    def test_query_session_id_propagation(
        self, test_client: TestClient, valid_jwt_token: str
    ) -> None:
        """Session ID passed in the request is tracked in response metadata."""
        headers = {"Authorization": f"Bearer {valid_jwt_token}"}
        payload = {
            "user_query": "SELECT 1",
            "session_id": "sess-uuid-9876",
        }
        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = ReportPlanSchema(
                thought_process="Simple probe.",
                tables_used=[],
                metrics_applied=[],
                sql_query="SELECT 1 AS val",
                suggested_visualization="table",
            )
            resp = test_client.post("/api/v1/reports/query", json=payload, headers=headers)
            assert resp.status_code == 200
            # If session_id is echoed in response or metadata
            res_json = resp.json()
            if "session_id" in res_json:
                assert res_json["session_id"] == "sess-uuid-9876"


# =====================================================================
# 4. 2-Step Self-Correction State Machine Tests
# =====================================================================

class TestTwoStepSelfCorrectionStateMachine:
    def test_single_retry_recovery_from_database_column_error(
        self, test_client: TestClient, valid_jwt_token: str
    ) -> None:
        """Attempt 1 produces bad column in SQL; Attempt 2 corrects SQL with hint and succeeds."""
        headers = {"Authorization": f"Bearer {valid_jwt_token}"}
        payload = {"user_query": "What is our total revenue?"}

        plan_bad = ReportPlanSchema(
            thought_process="Attempt 1: bad column.",
            tables_used=["voucher"],
            metrics_applied=[],
            sql_query="SELECT non_existent_bad_column FROM voucher",
            suggested_visualization="table",
        )
        plan_good = ReportPlanSchema(
            thought_process="Attempt 2: corrected to TotalAmount.",
            tables_used=["voucher"],
            metrics_applied=["total_sales_revenue"],
            sql_query="SELECT COALESCE(SUM(TotalAmount), 0) AS total_sales_revenue FROM voucher WHERE TransactionType = 'Sales'",
            suggested_visualization="kpi_card",
        )

        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = [plan_bad, plan_good]

            resp = test_client.post("/api/v1/reports/query", json=payload, headers=headers)
            assert resp.status_code == 200
            data = resp.json()

            assert data["status"] == "success"
            meta = data["execution_metadata"]
            assert meta["retries_used"] == 1
            assert len(meta["retry_history"]) == 1

            retry_entry = meta["retry_history"][0]
            assert retry_entry["attempt"] == 1
            assert "non_existent_bad_column" in retry_entry["failed_sql"]
            assert retry_entry["error_type"] in ("DATABASE_ERROR", "SCHEMA_ERROR", "ColumnNotFoundError")
            assert data["data"][0]["total_sales_revenue"] == 10368.0

    def test_single_retry_recovery_from_guardrail_ast_violation(
        self, test_client: TestClient, valid_jwt_token: str
    ) -> None:
        """Attempt 1 violates AST guardrail (DELETE statement); Attempt 2 corrects to SELECT."""
        headers = {"Authorization": f"Bearer {valid_jwt_token}"}
        payload = {"user_query": "Remove account and show me remaining accounts"}

        plan_bad = ReportPlanSchema(
            thought_process="Attempt 1: Attempting delete.",
            tables_used=["accounts"],
            metrics_applied=[],
            sql_query="DELETE FROM accounts WHERE id = 1",
            suggested_visualization="table",
        )
        plan_good = ReportPlanSchema(
            thought_process="Attempt 2: Corrected to read-only SELECT.",
            tables_used=["accounts"],
            metrics_applied=[],
            sql_query="SELECT id, name FROM accounts WHERE id = 1",
            suggested_visualization="table",
        )

        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = [plan_bad, plan_good]

            resp = test_client.post("/api/v1/reports/query", json=payload, headers=headers)
            assert resp.status_code == 200
            data = resp.json()

            assert data["status"] == "success"
            meta = data["execution_metadata"]
            assert meta["retries_used"] == 1
            assert len(meta["retry_history"]) == 1

            retry_entry = meta["retry_history"][0]
            assert retry_entry["attempt"] == 1
            assert retry_entry["error_type"] in ("GUARDRAIL_VIOLATION", "SQLSecurityViolation", "FORBIDDEN_STATEMENT")
            assert "DELETE" in retry_entry["failed_sql"]
            assert len(data["data"]) == 1

    def test_single_retry_recovery_from_restricted_table_access(
        self, test_client: TestClient, valid_jwt_token: str
    ) -> None:
        """Attempt 1 accesses restricted table gsp_credentials; Attempt 2 accesses allowed companies table."""
        headers = {"Authorization": f"Bearer {valid_jwt_token}"}
        payload = {"user_query": "Show integration credentials"}

        plan_restricted = ReportPlanSchema(
            thought_process="Attempt 1: accessing credentials.",
            tables_used=["gsp_credentials"],
            metrics_applied=[],
            sql_query="SELECT * FROM gsp_credentials",
            suggested_visualization="table",
        )
        plan_safe = ReportPlanSchema(
            thought_process="Attempt 2: accessing companies.",
            tables_used=["companies"],
            metrics_applied=[],
            sql_query="SELECT id, company_name FROM companies LIMIT 5",
            suggested_visualization="table",
        )

        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = [plan_restricted, plan_safe]

            resp = test_client.post("/api/v1/reports/query", json=payload, headers=headers)
            assert resp.status_code == 200
            data = resp.json()

            assert data["status"] == "success"
            assert data["execution_metadata"]["retries_used"] == 1
            assert "gsp_credentials" in data["execution_metadata"]["retry_history"][0]["failed_sql"]

    def test_double_retry_recovery_two_errors_then_success(
        self, test_client: TestClient, valid_jwt_token: str
    ) -> None:
        """Attempt 1 fails (syntax error), Attempt 2 fails (column error), Attempt 3 succeeds."""
        headers = {"Authorization": f"Bearer {valid_jwt_token}"}
        payload = {"user_query": "Total sales"}

        plan_1 = ReportPlanSchema(
            thought_process="Attempt 1 syntax error.",
            tables_used=["voucher"],
            metrics_applied=[],
            sql_query="SELECT FROM voucher WHERE",
            suggested_visualization="table",
        )
        plan_2 = ReportPlanSchema(
            thought_process="Attempt 2 bad column.",
            tables_used=["voucher"],
            metrics_applied=[],
            sql_query="SELECT invalid_col_again FROM voucher",
            suggested_visualization="table",
        )
        plan_3 = ReportPlanSchema(
            thought_process="Attempt 3 valid query.",
            tables_used=["voucher"],
            metrics_applied=["total_sales_revenue"],
            sql_query="SELECT COALESCE(SUM(TotalAmount), 0) AS total_sales_revenue FROM voucher WHERE TransactionType = 'Sales'",
            suggested_visualization="kpi_card",
        )

        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = [plan_1, plan_2, plan_3]

            resp = test_client.post("/api/v1/reports/query", json=payload, headers=headers)
            assert resp.status_code == 200
            data = resp.json()

            assert data["status"] == "success"
            meta = data["execution_metadata"]
            assert meta["retries_used"] == 2
            assert len(meta["retry_history"]) == 2
            assert meta["retry_history"][0]["attempt"] == 1
            assert meta["retry_history"][1]["attempt"] == 2
            assert data["data"][0]["total_sales_revenue"] == 10368.0

    def test_exhausted_retries_returns_structured_error_without_crash(
        self, test_client: TestClient, valid_jwt_token: str
    ) -> None:
        """All 3 attempts fail. Must return structured response with status='error' without crashing."""
        headers = {"Authorization": f"Bearer {valid_jwt_token}"}
        payload = {"user_query": "Unrecoverable query"}

        plan_bad = ReportPlanSchema(
            thought_process="Persistently invalid query.",
            tables_used=["voucher"],
            metrics_applied=[],
            sql_query="SELECT bad_col FROM voucher",
            suggested_visualization="table",
        )

        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = [plan_bad, plan_bad, plan_bad]

            resp = test_client.post("/api/v1/reports/query", json=payload, headers=headers)
            assert resp.status_code in (200, 400, 422)  # Structured response, NOT 500
            data = resp.json()

            assert data["status"] == "error"
            assert data["error"] is not None
            assert data["data"] == []
            meta = data["execution_metadata"]
            assert meta["retries_used"] >= 2
            assert len(meta["retry_history"]) >= 2

    def test_llm_refusal_plan_terminates_immediately_without_retries(
        self, test_client: TestClient, valid_jwt_token: str
    ) -> None:
        """Explicit model refusal (sql_query=None, error='...') stops immediately with 0 retries."""
        headers = {"Authorization": f"Bearer {valid_jwt_token}"}
        payload = {"user_query": "Drop all database tables"}

        plan_refusal = ReportPlanSchema(
            thought_process="Refusing destructive operation.",
            tables_used=[],
            metrics_applied=[],
            sql_query=None,
            suggested_visualization="table",
            error="Destructive DDL operations are strictly forbidden.",
        )

        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = plan_refusal

            resp = test_client.post("/api/v1/reports/query", json=payload, headers=headers)
            assert resp.status_code == 200
            data = resp.json()

            assert data["status"] in ("error", "refused")
            assert "forbidden" in data["error"].lower()
            assert data["execution_metadata"]["retries_used"] == 0
            assert data["execution_metadata"]["retry_history"] == []


# =====================================================================
# 5. Transparency & Metadata Tests
# =====================================================================

class TestExecutionMetadataTransparency:
    def test_execution_metadata_complete_schema_on_success(
        self, test_client: TestClient, valid_jwt_token: str
    ) -> None:
        """Successful response must contain all 8 execution_metadata fields."""
        headers = {"Authorization": f"Bearer {valid_jwt_token}"}
        payload = {"user_query": "SELECT 1"}

        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = ReportPlanSchema(
                thought_process="Testing metadata completeness.",
                tables_used=["accounts"],
                metrics_applied=["party_unpaid_balance"],
                sql_query="SELECT id, name FROM accounts LIMIT 1",
                suggested_visualization="table",
            )
            resp = test_client.post("/api/v1/reports/query", json=payload, headers=headers)
            assert resp.status_code == 200
            meta = resp.json()["execution_metadata"]

            required_keys = [
                "thought_process",
                "tables_used",
                "metrics_applied",
                "verified_sql",
                "retries_used",
                "retry_history",
                "latency_ms",
                "active_mode",
            ]
            for key in required_keys:
                assert key in meta, f"Missing required metadata key: {key}"

            assert meta["thought_process"] == "Testing metadata completeness."
            assert "accounts" in meta["tables_used"]
            assert "party_unpaid_balance" in meta["metrics_applied"]
            assert "SELECT" in meta["verified_sql"].upper()
            assert meta["retries_used"] == 0
            assert isinstance(meta["retry_history"], list)
            assert meta["latency_ms"] > 0

    def test_execution_metadata_complete_schema_on_failure(
        self, test_client: TestClient, valid_jwt_token: str
    ) -> None:
        """Failed response must still contain complete execution_metadata."""
        headers = {"Authorization": f"Bearer {valid_jwt_token}"}
        payload = {"user_query": "Failing query metadata test"}

        plan_bad = ReportPlanSchema(
            thought_process="Failure metadata test.",
            tables_used=["voucher"],
            metrics_applied=[],
            sql_query="SELECT non_existent_col FROM voucher",
            suggested_visualization="table",
        )

        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = plan_bad

            resp = test_client.post("/api/v1/reports/query", json=payload, headers=headers)
            meta = resp.json()["execution_metadata"]
            assert meta is not None
            assert "retry_history" in meta
            assert "latency_ms" in meta
            assert meta["retries_used"] >= 1

    def test_retry_history_structure_and_error_classification(
        self, test_client: TestClient, valid_jwt_token: str
    ) -> None:
        """Each entry in retry_history must include attempt, error_type, error_message, failed_sql."""
        headers = {"Authorization": f"Bearer {valid_jwt_token}"}
        payload = {"user_query": "Retry history structure test"}

        plan_bad = ReportPlanSchema(
            thought_process="Bad query.",
            tables_used=["voucher"],
            metrics_applied=[],
            sql_query="SELECT bad_syntax FROM voucher",
            suggested_visualization="table",
        )
        plan_good = ReportPlanSchema(
            thought_process="Good query.",
            tables_used=["voucher"],
            metrics_applied=[],
            sql_query="SELECT VoucherId FROM voucher LIMIT 1",
            suggested_visualization="table",
        )

        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = [plan_bad, plan_good]

            resp = test_client.post("/api/v1/reports/query", json=payload, headers=headers)
            item = resp.json()["execution_metadata"]["retry_history"][0]

            assert "attempt" in item
            assert "error_type" in item
            assert "error_message" in item
            assert "failed_sql" in item
            assert item["attempt"] == 1


# =====================================================================
# 6. Dynamic UI Schema Derivation Tests
# =====================================================================

class TestDynamicUISchemaDerivation:
    def test_ui_schema_kpi_grid_derivation(
        self, test_client: TestClient, valid_jwt_token: str
    ) -> None:
        """Single row with numeric aggregates derives suggested_visualization='kpi_grid'."""
        headers = {"Authorization": f"Bearer {valid_jwt_token}"}
        payload = {"user_query": "Sales KPI"}

        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = ReportPlanSchema(
                thought_process="Single row metric.",
                tables_used=["voucher"],
                metrics_applied=["total_sales_revenue"],
                sql_query="SELECT 10368.0 AS total_revenue, 3 AS invoice_count",
                suggested_visualization="kpi_card",
            )
            resp = test_client.post("/api/v1/reports/query", json=payload, headers=headers)
            ui = resp.json()["ui_schema"]
            assert ui["suggested_visualization"] in ("kpi_grid", "kpi_card")
            assert len(ui["kpis"]) >= 1

    def test_ui_schema_table_and_column_types_derivation(
        self, test_client: TestClient, valid_jwt_token: str
    ) -> None:
        """Multi-row tabular data derives column types (currency, string, date)."""
        headers = {"Authorization": f"Bearer {valid_jwt_token}"}
        payload = {"user_query": "Account list"}

        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = ReportPlanSchema(
                thought_process="Multi-row tabular query.",
                tables_used=["accounts"],
                metrics_applied=[],
                sql_query="SELECT id, name, unpaid_amount, created_at FROM accounts LIMIT 5",
                suggested_visualization="table",
            )
            resp = test_client.post("/api/v1/reports/query", json=payload, headers=headers)
            ui = resp.json()["ui_schema"]
            assert ui["suggested_visualization"] == "table"
            cols = {c["key"]: c for c in ui["columns"]}

            if "unpaid_amount" in cols:
                assert cols["unpaid_amount"]["type"] in ("currency", "number")
            if "name" in cols:
                assert cols["name"]["type"] == "string"

    def test_ui_schema_empty_result_derivation(
        self, test_client: TestClient, valid_jwt_token: str
    ) -> None:
        """Query returning 0 rows preserves column headers with empty kpis."""
        headers = {"Authorization": f"Bearer {valid_jwt_token}"}
        payload = {"user_query": "No matches query"}

        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = ReportPlanSchema(
                thought_process="Empty result set.",
                tables_used=["accounts"],
                metrics_applied=[],
                sql_query="SELECT id, name FROM accounts WHERE 1=0",
                suggested_visualization="table",
            )
            resp = test_client.post("/api/v1/reports/query", json=payload, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["data"] == []
            assert data["ui_schema"]["kpis"] == []


# =====================================================================
# 7. Timeout & Latency Resilience Tests
# =====================================================================

class TestTimeoutAndResilience:
    def test_llm_timeout_returns_structured_error(
        self, test_client: TestClient, valid_jwt_token: str
    ) -> None:
        """LLMTimeoutError is converted to structured error response with latency recorded."""
        headers = {"Authorization": f"Bearer {valid_jwt_token}"}
        payload = {"user_query": "Slow query that hangs"}

        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = LLMTimeoutError("Inference timed out after 60s")

            resp = test_client.post("/api/v1/reports/query", json=payload, headers=headers)
            assert resp.status_code in (200, 504, 408)
            data = resp.json()
            assert data["status"] == "error"
            assert "timeout" in data["error"].lower()
            assert data["execution_metadata"]["latency_ms"] >= 0

    def test_database_statement_timeout_retry_behavior(
        self, test_client: TestClient, valid_jwt_token: str
    ) -> None:
        """Database statement timeout triggers retry or structured error response."""
        headers = {"Authorization": f"Bearer {valid_jwt_token}"}
        payload = {"user_query": "Query that causes DB timeout"}

        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = ReportPlanSchema(
                thought_process="Timeout simulation.",
                tables_used=["voucher"],
                metrics_applied=[],
                sql_query="SELECT * FROM voucher",
                suggested_visualization="table",
            )
            with patch.object(DatabaseExecutor, "execute_query", side_effect=StatementTimeoutError("Timeout")):
                resp = test_client.post("/api/v1/reports/query", json=payload, headers=headers)
                assert resp.status_code in (200, 400, 504)
                data = resp.json()
                assert data["status"] == "error"
                assert "timeout" in data["error"].lower()


# =====================================================================
# 8. Input Validation & Security Edge Cases
# =====================================================================

class TestInputValidationAndSecurityEdgeCases:
    def test_empty_or_whitespace_user_query_returns_422(
        self, test_client: TestClient, valid_jwt_token: str
    ) -> None:
        """Missing or whitespace-only user_query returns HTTP 422 Unprocessable Entity."""
        headers = {"Authorization": f"Bearer {valid_jwt_token}"}
        resp = test_client.post("/api/v1/reports/query", json={"user_query": "   "}, headers=headers)
        assert resp.status_code == 422

    def test_missing_required_fields_returns_422(
        self, test_client: TestClient, valid_jwt_token: str
    ) -> None:
        """Empty payload {} returns HTTP 422."""
        headers = {"Authorization": f"Bearer {valid_jwt_token}"}
        resp = test_client.post("/api/v1/reports/query", json={}, headers=headers)
        assert resp.status_code == 422

    def test_sql_injection_attempt_in_user_query_handled_safely(
        self, test_client: TestClient, valid_jwt_token: str
    ) -> None:
        """SQL injection strings in user_query are safely processed without executing injection."""
        headers = {"Authorization": f"Bearer {valid_jwt_token}"}
        payload = {"user_query": "'; DROP TABLE accounts; --"}

        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = ReportPlanSchema(
                thought_process="Refusing injection.",
                tables_used=[],
                metrics_applied=[],
                sql_query=None,
                suggested_visualization="table",
                error="SQL injection attempt rejected.",
            )
            resp = test_client.post("/api/v1/reports/query", json=payload, headers=headers)
            assert resp.status_code == 200
            assert resp.json()["status"] in ("error", "refused")

    def test_invalid_json_body_returns_422(
        self, test_client: TestClient, valid_jwt_token: str
    ) -> None:
        """Invalid non-JSON or malformed payload returns HTTP 422 Unprocessable Entity."""
        headers = {
            "Authorization": f"Bearer {valid_jwt_token}",
            "Content-Type": "application/json",
        }
        resp = test_client.post("/api/v1/reports/query", content="bad json content", headers=headers)
        assert resp.status_code == 422

    def test_ast_guardrail_injects_limit_1000_if_omitted(
        self, test_client: TestClient, valid_jwt_token: str
    ) -> None:
        """If query omits LIMIT, AST Guardrail automatically injects LIMIT 1000."""
        headers = {"Authorization": f"Bearer {valid_jwt_token}"}
        payload = {"user_query": "List companies"}

        with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = ReportPlanSchema(
                thought_process="Companies query without explicit limit.",
                tables_used=["companies"],
                metrics_applied=[],
                sql_query="SELECT id, company_name FROM companies",
                suggested_visualization="table",
            )
            resp = test_client.post("/api/v1/reports/query", json=payload, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "success"
            verified = data["execution_metadata"]["verified_sql"]
            assert "LIMIT" in verified.upper()
