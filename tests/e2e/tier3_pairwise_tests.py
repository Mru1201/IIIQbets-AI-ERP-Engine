"""Tier 3 E2E Pairwise Combinatorial Interaction Tests.

Covers pairwise combinations across:
1. Modules (sales, finance, inventory, tax, accounting, orders, all) x Auth Modes (JWT vs DEV_MODE headers vs tokenless).
2. Visualization Types (kpi_grid, bar_chart, line_chart, donut_chart, table) x Self-Correction Retries (0, 1, 2 retries).
3. LLM Providers (vllm, ollama, openai, gemini, openrouter) x Output Formats (direct JSON, code fence, outer braces, trailing comma).
4. SubSchemaRouter Modes (bypass vs forced prune) x Module Queries x Guardrail Star Expansion.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient
import httpx
import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent
ORCHESTRATOR_ROOT = WORKSPACE_ROOT / "erp-ai-orchestrator"
PIPELINE_ROOT = WORKSPACE_ROOT / "erp-ai-model-pipeline"

for p in (str(ORCHESTRATOR_ROOT), str(PIPELINE_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.api.v1.reports import get_db_executor, get_llm_client
from app.auth.jwt_handler import create_access_token
from app.config import DEFAULT_METASTORE_PATH, DEFAULT_SQL_DUMP_PATH, Settings
from app.connectors.executor import DatabaseExecutor
from app.inference.llm_client import LLMClient
from app.main import app
from app.metastore.loader import load_metastore
from app.metastore.router import SubSchemaRouter
from app.schemas.report_plan import ReportPlanSchema
from app.security.guardrails import validate_and_sanitize_sql


@pytest.fixture(scope="module")
def metastore() -> dict[str, Any]:
    return load_metastore(DEFAULT_METASTORE_PATH)


@pytest.fixture(scope="module")
def sqlite_executor() -> DatabaseExecutor:
    ex = DatabaseExecutor(mode="sqlite", sql_dump_path=DEFAULT_SQL_DUMP_PATH)
    ex.provision_sqlite()
    return ex


# =========================================================================== #
# 1. Pairwise: Module Scope x Auth Mode (7 modules x 3 auth modes = 21 tests)
# =========================================================================== #
MODULE_SQL_MAP: dict[str, tuple[str, list[str], str]] = {
    "sales": (
        "Show total sales revenue",
        ["voucher"],
        "SELECT COALESCE(SUM(TotalAmount), 0) AS total_sales_revenue FROM voucher WHERE TransactionType = 'Sales'",
    ),
    "finance": (
        "Show total operating expenses",
        ["expensive"],
        "SELECT COALESCE(SUM(amount), 0) AS total_expenses FROM expensive",
    ),
    "inventory": (
        "Show batch stock quantities",
        ["batches"],
        "SELECT batch_number, CAST(quantity AS DECIMAL(15,2)) AS stock_qty FROM batches LIMIT 5",
    ),
    "tax": (
        "Show GST tax summary on sales vouchers",
        ["voucher"],
        "SELECT VoucherID, Subtotal, TaxAmount, TotalAmount FROM voucher WHERE TransactionType = 'Sales'",
    ),
    "accounting": (
        "Show chart of account groups",
        ["accountgroup"],
        "SELECT accountgroup_id, AccountsGroupName FROM accountgroup LIMIT 5",
    ),
    "orders": (
        "Show retailer credit periods",
        ["credit_periods"],
        "SELECT id, credit_period, CAST(credit_percentage AS DECIMAL(15,2)) AS pct FROM credit_periods LIMIT 5",
    ),
    "all": (
        "Show company and brand count",
        ["companies"],
        "SELECT COUNT(*) AS company_count FROM companies",
    ),
}


class TestPairwiseModuleAndAuthModes:
    @pytest.mark.parametrize("module_scope", ["sales", "finance", "inventory", "tax", "accounting", "orders", "all"])
    @pytest.mark.parametrize("auth_mode", ["jwt_bearer", "dev_headers", "dev_tokenless"])
    def test_p01_module_x_auth_mode_execution(
        self,
        sqlite_executor: DatabaseExecutor,
        module_scope: str,
        auth_mode: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        query_text, tables, sql = MODULE_SQL_MAP[module_scope]
        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.last_token_usage = {"prompt_tokens": 30, "completion_tokens": 15, "total_tokens": 45}
        mock_llm.generate_report_plan_async = AsyncMock(
            return_value=ReportPlanSchema(
                thought_process=f"Executing {module_scope} query under {auth_mode}",
                tables_used=tables,
                sql_query=sql,
            )
        )
        app.dependency_overrides[get_llm_client] = lambda: mock_llm
        app.dependency_overrides[get_db_executor] = lambda: sqlite_executor

        headers: dict[str, str] = {}
        if auth_mode == "jwt_bearer":
            monkeypatch.setattr("app.auth.jwt_handler.is_dev_mode", lambda: False)
            token = create_access_token(
                {"sub": "jwt_user", "tenant_id": "shree_shashwatraj_agro", "role": "admin"},
            )
            headers["Authorization"] = f"Bearer {token}"
        elif auth_mode == "dev_headers":
            monkeypatch.setattr("app.auth.jwt_handler.is_dev_mode", lambda: True)
            headers["X-Tenant-ID"] = "shree_shashwatraj_agro"
            headers["X-User-ID"] = "dev_header_user"
        else:
            monkeypatch.setattr("app.auth.jwt_handler.is_dev_mode", lambda: True)

        try:
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/reports/query",
                    headers=headers,
                    json={
                        "tenant_id": "shree_shashwatraj_agro",
                        "module_scope": module_scope,
                        "user_query": query_text,
                    },
                )
                assert resp.status_code == 200
                body = resp.json()
                assert body["status"] == "success"
                assert len(body["data"]) >= 1
        finally:
            app.dependency_overrides.clear()


# =========================================================================== #
# 2. Pairwise: Visualization Type x Self-Correction Retries (5 x 3 = 15 tests)
# =========================================================================== #
VIS_QUERY_SPECS: dict[str, tuple[str, str]] = {
    "kpi_grid": (
        "kpi_card",
        "SELECT COALESCE(SUM(TotalAmount), 0) AS total_sales_revenue FROM voucher WHERE TransactionType = 'Sales'",
    ),
    "line_chart": (
        "line",
        "SELECT DATE(created_at) AS created_date, CAST(quantity AS DECIMAL(15,2)) AS stock_qty FROM batches LIMIT 5",
    ),
    "donut_chart": (
        "pie",
        "SELECT TransactionType, COUNT(*) AS voucher_count FROM voucher GROUP BY TransactionType",
    ),
    "bar_chart": (
        "bar",
        "SELECT TransactionType, SUM(TotalAmount) AS total_amount FROM voucher GROUP BY TransactionType",
    ),
    "table": (
        "table",
        "SELECT id, name, business_name, role, CAST(unpaid_amount AS DECIMAL(15,2)) AS unpaid_balance FROM accounts WHERE CAST(unpaid_amount AS DECIMAL(15,2)) > 0",
    ),
}


class TestPairwiseVisualizationAndRetries:
    @pytest.mark.parametrize("expected_vis", ["kpi_grid", "line_chart", "donut_chart", "bar_chart", "table"])
    @pytest.mark.parametrize("retry_count", [0, 1, 2])
    def test_p02_visualization_x_retry_trajectory(
        self,
        sqlite_executor: DatabaseExecutor,
        expected_vis: str,
        retry_count: int,
    ) -> None:
        llm_vis_hint, valid_sql = VIS_QUERY_SPECS[expected_vis]
        plans: list[ReportPlanSchema] = []

        if retry_count >= 1:
            plans.append(
                ReportPlanSchema(
                    thought_process="Attempt 1 bad column",
                    tables_used=["voucher"],
                    sql_query="SELECT non_existent_col_1 FROM voucher",
                    suggested_visualization=llm_vis_hint,  # type: ignore[arg-type]
                )
            )
        if retry_count >= 2:
            plans.append(
                ReportPlanSchema(
                    thought_process="Attempt 2 bad table",
                    tables_used=["non_existent_table_2"],
                    sql_query="SELECT id FROM non_existent_table_2",
                    suggested_visualization=llm_vis_hint,  # type: ignore[arg-type]
                )
            )

        plans.append(
            ReportPlanSchema(
                thought_process="Final valid plan",
                tables_used=["voucher", "accounts"],
                sql_query=valid_sql,
                suggested_visualization=llm_vis_hint,  # type: ignore[arg-type]
            )
        )

        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.last_token_usage = {}
        mock_llm.generate_report_plan_async = AsyncMock(side_effect=plans)
        app.dependency_overrides[get_llm_client] = lambda: mock_llm
        app.dependency_overrides[get_db_executor] = lambda: sqlite_executor

        try:
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/reports/query",
                    json={"user_query": f"Generate {expected_vis} with {retry_count} retries"},
                )
                assert resp.status_code == 200
                body = resp.json()
                assert body["status"] == "success"
                assert body["execution_metadata"]["retries_used"] == retry_count
                assert len(body["execution_metadata"]["retry_history"]) == retry_count
                assert body["ui_schema"]["suggested_visualization"] == expected_vis
        finally:
            app.dependency_overrides.clear()


# =========================================================================== #
# 3. Pairwise: LLM Provider x Raw JSON Formatting Tiers (5 x 4 = 20 tests)
# =========================================================================== #
RAW_JSON_TIERS: dict[str, str] = {
    "tier1_clean": json.dumps({
        "thought_process": "Direct JSON",
        "tables_used": ["accounts"],
        "metrics_applied": [],
        "sql_query": "SELECT id, name FROM accounts LIMIT 3",
        "suggested_visualization": "table",
        "error": None,
    }),
    "tier2_fenced": "Here is the query:\n```json\n" + json.dumps({
        "thought_process": "Fenced JSON",
        "tables_used": ["accounts"],
        "metrics_applied": [],
        "sql_query": "SELECT id, name FROM accounts LIMIT 3",
        "suggested_visualization": "table",
        "error": None,
    }) + "\n```",
    "tier3_prose_wrapped": "Sure! " + json.dumps({
        "thought_process": "Prose wrapped",
        "tables_used": ["accounts"],
        "metrics_applied": [],
        "sql_query": "SELECT id, name FROM accounts LIMIT 3",
        "suggested_visualization": "table",
        "error": None,
    }) + " Hope this helps.",
    "tier4_trailing_comma": (
        '{"thought_process": "Trailing comma", "tables_used": ["accounts", ], '
        '"metrics_applied": [], "sql_query": "SELECT id, name FROM accounts LIMIT 3", '
        '"suggested_visualization": "table", "error": null, }'
    ),
}


class TestPairwiseProviderAndJSONTiers:
    @pytest.mark.parametrize("provider", ["vllm", "ollama", "openai", "gemini", "openrouter"])
    @pytest.mark.parametrize("json_tier", ["tier1_clean", "tier2_fenced", "tier3_prose_wrapped", "tier4_trailing_comma"])
    def test_p03_provider_x_json_recovery_tier(
        self,
        metastore: dict[str, Any],
        provider: str,
        json_tier: str,
    ) -> None:
        raw_text = RAW_JSON_TIERS[json_tier]
        mock_http = MagicMock(spec=httpx.Client)
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
            "choices": [{"message": {"content": raw_text}}],
        }
        mock_http.post.return_value = mock_resp

        client = LLMClient(
            settings=Settings(llm_provider=provider, llm_api_key="test-key"),  # type: ignore[arg-type]
            metastore=metastore,
            http_client=mock_http,
        )
        plan = client.generate_report_plan("List 3 accounts", validate_ast=True)
        assert plan.is_refusal is False
        assert plan.sql_query == "SELECT id, name FROM accounts LIMIT 3"


# =========================================================================== #
# 4. Pairwise: SubSchemaRouter Mode x Module Queries (2 x 5 = 10 tests)
# =========================================================================== #
ROUTER_QUERIES: list[tuple[str, str]] = [
    ("What is our net profit and gross profit?", "voucher"),
    ("Show live batch stock on hand and product names", "batches"),
    ("Show sundry debtors with unpaid balance", "accounts"),
    ("Show operating expenses from expensive table", "expensive"),
    ("Show bank direct deposit entries", "direct_deposit"),
]


class TestPairwiseRouterModesAndQueries:
    @pytest.mark.parametrize("force_prune", [False, True])
    @pytest.mark.parametrize("query_text,expected_table", ROUTER_QUERIES)
    def test_p04_router_mode_x_domain_query(
        self,
        metastore: dict[str, Any],
        force_prune: bool,
        query_text: str,
        expected_table: str,
    ) -> None:
        router = SubSchemaRouter(metastore=metastore, force_pure_python=True)
        pruned, meta = router.route_and_prune(query_text, force_prune=force_prune, top_k=5, max_k=10)
        assert meta["bypassed"] is (not force_prune)
        assert expected_table in pruned["tables"]
        assert "accounts" in pruned["tables"]
        sanitized = validate_and_sanitize_sql(f"SELECT * FROM {expected_table} LIMIT 5", pruned)
        assert "LIMIT 5" in sanitized
