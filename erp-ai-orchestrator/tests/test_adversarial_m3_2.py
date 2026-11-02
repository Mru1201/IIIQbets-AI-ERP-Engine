"""Adversarial Challenge Test Suite for Milestone 3 (challenger_m3_2).

Focus Areas:
1. Flexible JWT & DEV_MODE bypass boundary conditions:
   - Malformed base64, missing segments, invalid token structures -> HTTP 401
   - Expired tokens, future tokens, invalid secret signatures -> HTTP 401
   - Alg confusion ('none', 'RS256') -> HTTP 401
   - Missing token when DEV_MODE=False -> HTTP 401 (header overrides ignored)
   - Missing token when DEV_MODE=True -> HTTP 200 with default context
   - Header overrides (X-Tenant-ID / X-User-ID / X-Role) in DEV_MODE=True
   - Malformed/expired token in DEV_MODE=True -> MUST STILL BE 401
   - Token claims taking priority over request body tenant_id
2. Dynamic UI Schema derivation under edge case result sets:
   - 0 rows (empty dataset) with various column names
   - 1 row with numeric/currency vs string-only
   - Multiple rows (key-value metrics vs wide table vs entity list)
   - Negative numbers (net loss, negative profit, negative balances)
   - Null columns (all null, leading null, trailing null)
   - Currency vs date vs number vs boolean type inference
   - Extreme numbers, large decimals, division by zero resilience
   - Visualization suggestions: kpi_grid, table, line_chart, bar_chart, donut_chart, invalid suggestions
3. Empirical Bug Demonstrations:
   - Bug 1: Suffix/Dimension collision where counts/quantities with substrings 'sales' or 'total' are misclassified as currency ('3.00 INR')
   - Bug 2: None-fallback in suggested visualization short-circuits dynamic chart inference to 'table'
   - Bug 3: Explicit LLM 'bar_chart' suggestion overridden by 'donut_chart' for 2-6 categories
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient

from app.api.v1.ui_deriver import (
    CURRENCY_KEYWORDS,
    DATE_KEYWORDS,
    NUMBER_KEYWORDS,
    derive_column_defs,
    derive_kpi_cards,
    derive_suggested_visualization,
    derive_ui_schema,
    format_column_label,
    format_kpi_value,
    infer_column_type,
)
from app.auth.jwt_handler import create_access_token, verify_token
from app.config import Settings, get_settings
from app.connectors.executor import ExecutionResult
from app.main import app
from app.schemas.api_models import ColumnDef, KPICard, QueryResponse, UISchema
from app.schemas.report_plan import ReportPlanSchema


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def test_settings() -> Settings:
    return get_settings()


# =====================================================================
# Test Class 1: Adversarial JWT & DEV_MODE Security Boundaries
# =====================================================================

class TestAdversarialAuthAndDevModeBoundaries:
    """Rigorous boundary testing for JWT authentication and dev-mode bypass."""

    def test_malformed_token_formats_rejected_with_401(self, client: TestClient):
        """Verify malformed authorization headers are strictly rejected with 401."""
        malformed_headers = [
            "Bearer",                         # missing token
            "Bearer ",                        # trailing space only
            "Bearer    ",                     # whitespace token
            "Bearer a.b",                     # missing 3rd segment
            "Bearer a",                       # single segment
            "Bearer a.b.c.d.e",               # too many segments
            "Bearer !@#$%^&*()_+",            # invalid base64 characters
            "Bearer eyJhbGciOiJIUzI1NiJ9",   # truncated header only
            "Bearer token_part1 token_part2", # multiple tokens
            "Basic dXNlcjpwYXNz",             # wrong auth scheme
            "Token some_random_token",        # wrong scheme
            "bearer",                         # lowercase scheme without token
        ]

        for auth_hdr in malformed_headers:
            resp = client.post(
                "/api/v1/reports/query",
                json={"user_query": "What is our total revenue?"},
                headers={"Authorization": auth_hdr},
            )
            assert resp.status_code == 401, (
                f"Expected 401 for header '{auth_hdr}', got {resp.status_code}: {resp.text}"
            )
            assert "WWW-Authenticate" in resp.headers
            assert "Bearer" in resp.headers["WWW-Authenticate"]

    def test_case_insensitive_bearer_with_valid_token_accepted(self, client: TestClient, test_settings: Settings):
        """Verify RFC 6750 case-insensitive Bearer prefix handling."""
        token = create_access_token({"sub": "test_user", "tenant_id": "test_tenant"})
        for prefix in ["Bearer", "bearer", "BEARER"]:
            resp = client.post(
                "/api/v1/reports/query",
                json={"user_query": "What is our total revenue?"},
                headers={"Authorization": f"{prefix} {token}"},
            )
            assert resp.status_code != 401, f"Expected auth success for prefix '{prefix}', got 401"

    def test_wrong_secret_signature_rejected_with_401(self, client: TestClient):
        """Tokens signed with unauthorized secret must be rejected."""
        attacker_token = jwt.encode(
            {"sub": "attacker", "tenant_id": "attacker_tenant", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
            key="completely-wrong-secret-key-1234567890",
            algorithm="HS256",
        )
        resp = client.post(
            "/api/v1/reports/query",
            json={"user_query": "Show financial summary"},
            headers={"Authorization": f"Bearer {attacker_token}"},
        )
        assert resp.status_code == 401
        assert "Invalid authorization token" in resp.json()["detail"]

    def test_algorithm_confusion_none_rejected_with_401(self, client: TestClient):
        """Tokens signed with 'none' algorithm must be rejected."""
        unsecured_token = jwt.encode(
            {"sub": "attacker", "tenant_id": "attacker_tenant"},
            key="",
            algorithm="none",
        )
        resp = client.post(
            "/api/v1/reports/query",
            json={"user_query": "Show financial summary"},
            headers={"Authorization": f"Bearer {unsecured_token}"},
        )
        assert resp.status_code == 401

    def test_algorithm_confusion_asymmetric_rejected_with_401(self, client: TestClient):
        """Tokens specifying unsupported/mismatched algorithms must be rejected."""
        fake_rs256_token = jwt.encode(
            {"sub": "attacker", "tenant_id": "attacker_tenant"},
            key="some-fake-key",
            algorithm="HS384",
        )
        resp = client.post(
            "/api/v1/reports/query",
            json={"user_query": "Show financial summary"},
            headers={"Authorization": f"Bearer {fake_rs256_token}"},
        )
        assert resp.status_code == 401

    def test_expired_token_rejected_with_401(self, client: TestClient, test_settings: Settings):
        """Expired tokens must be rejected with 401, including explicit detail."""
        past = datetime.now(timezone.utc) - timedelta(seconds=10)
        expired_token = jwt.encode(
            {"sub": "expired_user", "tenant_id": "default", "exp": past},
            key=test_settings.jwt_secret,
            algorithm=test_settings.jwt_algorithm,
        )
        resp = client.post(
            "/api/v1/reports/query",
            json={"user_query": "Show financial summary"},
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert resp.status_code == 401
        assert "Token has expired" in resp.json()["detail"]

    def test_dev_mode_false_blocks_all_unauthenticated_requests(self, client: TestClient):
        """When DEV_MODE=False, missing token must return 401, ignoring header overrides."""
        with patch("app.auth.jwt_handler.is_dev_mode", return_value=False):
            # 1. Missing token without headers
            resp1 = client.post(
                "/api/v1/reports/query",
                json={"user_query": "What is our total revenue?"},
            )
            assert resp1.status_code == 401
            assert "Missing authorization token" in resp1.json()["detail"]

            # 2. Missing token with header spoofing
            resp2 = client.post(
                "/api/v1/reports/query",
                json={"user_query": "What is our total revenue?"},
                headers={
                    "X-Tenant-ID": "spoofed_corp",
                    "X-User-ID": "admin",
                    "X-Role": "superadmin",
                },
            )
            assert resp2.status_code == 401
            assert "Missing authorization token" in resp2.json()["detail"]

    def test_dev_mode_true_permits_missing_token_and_header_overrides(self, client: TestClient):
        """When DEV_MODE=True, missing token succeeds and honors X-Tenant-ID / X-User-ID."""
        mock_plan = ReportPlanSchema(
            thought_process="Dev mode test plan",
            tables_used=["voucher"],
            metrics_applied=["total_sales_revenue"],
            sql_query="SELECT 1000.0 AS total_sales_revenue",
            suggested_visualization="kpi_card",
        )
        mock_result = ExecutionResult(
            columns=["total_sales_revenue"],
            rows=[{"total_sales_revenue": 1000.0}],
            execution_time_ms=5.0,
            row_count=1,
            dialect="sqlite",
        )

        with (
            patch("app.auth.jwt_handler.is_dev_mode", return_value=True),
            patch("app.api.v1.reports.LLMClient.generate_report_plan_async", new_callable=AsyncMock, return_value=mock_plan),
            patch("app.api.v1.reports.DatabaseExecutor.execute_query", return_value=mock_result),
        ):
            # Test default dev context
            resp_default = client.post(
                "/api/v1/reports/query",
                json={"user_query": "Total revenue"},
            )
            assert resp_default.status_code == 200
            data_default = resp_default.json()
            assert data_default["status"] == "success"

            # Test header overrides
            resp_override = client.post(
                "/api/v1/reports/query",
                json={"user_query": "Total revenue"},
                headers={
                    "X-Tenant-ID": "tenant_override_99",
                    "X-User-ID": "dev_analyst_01",
                    "X-Role": "manager",
                },
            )
            assert resp_override.status_code == 200
            data_override = resp_override.json()
            assert data_override["status"] == "success"

    def test_dev_mode_true_still_rejects_explicit_invalid_or_expired_tokens(
        self, client: TestClient, test_settings: Settings
    ):
        """DEV_MODE=True must NOT bypass an invalid or expired token if the client provided one."""
        with patch("app.auth.jwt_handler.is_dev_mode", return_value=True):
            # 1. Invalid signature token
            bad_token = jwt.encode(
                {"sub": "user"},
                key="bad-secret-key",
                algorithm="HS256",
            )
            resp_bad = client.post(
                "/api/v1/reports/query",
                json={"user_query": "Total revenue"},
                headers={
                    "Authorization": f"Bearer {bad_token}",
                    "X-Tenant-ID": "tenant_bypass",
                },
            )
            assert resp_bad.status_code == 401

            # 2. Expired token
            past = datetime.now(timezone.utc) - timedelta(minutes=5)
            expired_token = jwt.encode(
                {"sub": "user", "exp": past},
                key=test_settings.jwt_secret,
                algorithm=test_settings.jwt_algorithm,
            )
            resp_exp = client.post(
                "/api/v1/reports/query",
                json={"user_query": "Total revenue"},
                headers={
                    "Authorization": f"Bearer {expired_token}",
                    "X-Tenant-ID": "tenant_bypass",
                },
            )
            assert resp_exp.status_code == 401

    def test_token_claims_priority_over_untrusted_request_body_tenant_id(
        self, client: TestClient, test_settings: Settings
    ):
        """Authenticated token tenant_id must take priority over body payload tenant_id."""
        auth_token = jwt.encode(
            {"sub": "user_a", "tenant_id": "authenticated_tenant_a"},
            key=test_settings.jwt_secret,
            algorithm=test_settings.jwt_algorithm,
        )

        captured_tenant_id: list[str] = []

        async def fake_plan(user_query, tenant_id, **kwargs):
            captured_tenant_id.append(tenant_id)
            return ReportPlanSchema(
                thought_process="Test tenant isolation",
                tables_used=["voucher"],
                metrics_applied=["total_sales_revenue"],
                sql_query="SELECT 100 AS total_sales_revenue",
                suggested_visualization="kpi_card",
            )

        with (
            patch("app.api.v1.reports.LLMClient.generate_report_plan_async", side_effect=fake_plan),
            patch("app.api.v1.reports.DatabaseExecutor.execute_query", return_value=ExecutionResult(["total_sales_revenue"], [{"total_sales_revenue": 100}], 2.0, 1, "sqlite")),
        ):
            resp = client.post(
                "/api/v1/reports/query",
                json={
                    "user_query": "Total sales revenue",
                    "tenant_id": "malicious_spoofed_tenant_b",  # untrusted body
                },
                headers={"Authorization": f"Bearer {auth_token}"},
            )
            assert resp.status_code == 200
            assert len(captured_tenant_id) == 1
            assert captured_tenant_id[0] == "authenticated_tenant_a"


# =====================================================================
# Test Class 2: Dynamic UI Schema Derivation under Adversarial Edge Cases
# =====================================================================

class TestAdversarialUISchemaDerivation:
    """Stress-testing dynamic UI schema derivation across edge cases."""

    def test_zero_rows_empty_result_set(self):
        """Empty result set (0 rows) must derive valid 'table' schema without ZeroDivisionError."""
        columns = ["voucher_id", "party_name", "amount", "voucher_date"]
        data: list[dict[str, Any]] = []

        schema = derive_ui_schema(columns, data)

        assert schema.suggested_visualization == "table"
        assert len(schema.columns) == 4
        assert schema.kpis == []
        assert schema.filters == []

        col_map = {c.key: c for c in schema.columns}
        assert col_map["voucher_id"].type == "number"
        assert col_map["amount"].type == "currency"
        assert col_map["voucher_date"].type == "date"
        assert col_map["party_name"].type == "string"

    def test_zero_rows_and_zero_columns(self):
        """Completely empty response (0 columns, 0 rows) derives cleanly."""
        schema = derive_ui_schema([], [])
        assert schema.suggested_visualization == "table"
        assert schema.columns == []
        assert schema.kpis == []

    def test_single_row_positive_and_negative_metrics(self):
        """Single row with positive profit and negative net loss."""
        columns = ["gross_profit", "net_profit", "total_expenses"]
        data = [{"gross_profit": 55000.0, "net_profit": -43232.0, "total_expenses": 98232.0}]

        schema = derive_ui_schema(columns, data)

        assert schema.suggested_visualization == "kpi_grid"
        assert len(schema.kpis) == 3

        kpi_map = {k.label: k for k in schema.kpis}

        # Gross Profit: positive trend
        assert "Gross Profit" in kpi_map
        assert kpi_map["Gross Profit"].value == "55,000.00 INR"
        assert kpi_map["Gross Profit"].trend == "positive"

        # Net Profit (negative): negative trend
        assert "Net Profit" in kpi_map
        assert kpi_map["Net Profit"].value == "-43,232.00 INR"
        assert kpi_map["Net Profit"].trend == "negative"

        # Total Expenses (> 0): negative trend for expenses
        assert "Total Expenses" in kpi_map
        assert kpi_map["Total Expenses"].value == "98,232.00 INR"
        assert kpi_map["Total Expenses"].trend == "negative"

    def test_single_row_non_numeric_only_falls_back_to_table(self):
        """Single row with non-numeric data must NOT force kpi_grid without metrics."""
        columns = ["company_name", "status", "notes"]
        data = [{"company_name": "Acme Corp", "status": "Active", "notes": "Wholesale debtor"}]

        schema = derive_ui_schema(columns, data)
        assert schema.suggested_visualization == "table"
        assert schema.kpis == []

    def test_key_value_metric_table_negative_loss_derivation(self):
        """Key-value metric rows (e.g. 2 rows) with negative values."""
        columns = ["metric", "amount"]
        data = [
            {"metric": "Operating Revenue", "amount": 100000.0},
            {"metric": "Net Loss", "amount": -15000.0},
        ]

        schema = derive_ui_schema(columns, data)

        assert schema.suggested_visualization == "kpi_grid"
        assert len(schema.kpis) == 2

        assert schema.kpis[0].label == "Operating Revenue"
        assert schema.kpis[0].value == "100,000.00 INR"
        assert schema.kpis[0].trend == "positive"

        assert schema.kpis[1].label == "Net Loss"
        assert schema.kpis[1].value == "-15,000.00 INR"
        assert schema.kpis[1].trend == "negative"

    def test_multi_row_tabular_with_currency_and_summary_aggregations(self):
        """Multi-row tabular data derives summary cards (Total Records, Total Currency, Avg Currency)."""
        columns = ["party_name", "city", "unpaid_balance"]
        data = [
            {"party_name": "Debtor A", "city": "Mumbai", "unpaid_balance": 1000.0},
            {"party_name": "Debtor B", "city": "Pune", "unpaid_balance": 2000.0},
            {"party_name": "Debtor C", "city": "Nagpur", "unpaid_balance": 3000.0},
        ]

        schema = derive_ui_schema(columns, data)

        kpi_labels = [k.label for k in schema.kpis]

        assert "Total Records" in kpi_labels
        rec_card = next(k for k in schema.kpis if k.label == "Total Records")
        assert rec_card.value == 3

        assert "Total Unpaid Balance" in kpi_labels
        total_card = next(k for k in schema.kpis if k.label == "Total Unpaid Balance")
        assert total_card.value == "6,000.00 INR"

        assert "Avg Unpaid Balance" in kpi_labels
        avg_card = next(k for k in schema.kpis if k.label == "Avg Unpaid Balance")
        assert avg_card.value == "2,000.00 INR"

    def test_multi_row_without_currency_columns(self):
        """Multi-row entity list without any currency column does not crash and emits Total Records."""
        columns = ["item_code", "warehouse_name", "status"]
        data = [
            {"item_code": "ITM01", "warehouse_name": "WH-East", "status": "Available"},
            {"item_code": "ITM02", "warehouse_name": "WH-West", "status": "Reserved"},
        ]

        schema = derive_ui_schema(columns, data)
        assert len(schema.kpis) == 1
        assert schema.kpis[0].label == "Total Records"
        assert schema.kpis[0].value == 2

    def test_all_null_column_type_inference_and_aggregation(self):
        """Columns where all values are None should infer type gracefully and not fail aggregation."""
        columns = ["id", "amount", "notes"]
        data = [
            {"id": 1, "amount": None, "notes": None},
            {"id": 2, "amount": None, "notes": None},
        ]

        schema = derive_ui_schema(columns, data)

        col_map = {c.key: c for c in schema.columns}
        assert col_map["amount"].type == "currency"
        assert col_map["notes"].type == "string"

        total_card = next((k for k in schema.kpis if "Total" in k.label and "Amount" in k.label), None)
        if total_card:
            assert total_card.value == "0.00 INR"

    def test_leading_null_column_type_inference(self):
        """Column with leading None values followed by valid typed values."""
        date_type = infer_column_type("custom_date", [None, "2026-05-01", "2026-05-02"])
        assert date_type == "date"

        curr_type = infer_column_type("custom_rate", [None, 45.5, 99.0])
        assert curr_type == "number"

        bool_type = infer_column_type("active_flag", [None, True, False])
        assert bool_type == "boolean"

    def test_date_vs_currency_vs_number_detection_accuracy(self):
        """Test accuracy of column data type heuristics for clearly distinct keys."""
        # 1. Date objects and strings
        assert infer_column_type("txn_date", [date(2026, 1, 1)]) == "date"
        assert infer_column_type("txn_time", [datetime(2026, 1, 1, 12, 0, 0)]) == "date"
        assert infer_column_type("any_column", ["2026-04-17"]) == "date"
        assert infer_column_type("any_column", ["2026-04-17 18:30:00"]) == "date"

        # 2. Currency detection by keyword + numeric value
        assert infer_column_type("total_sales_revenue", [10368.0]) == "currency"
        assert infer_column_type("cogs", [7900.0]) == "currency"
        assert infer_column_type("gross_profit", [2468.0]) == "currency"
        assert infer_column_type("net_profit", [-43232.0]) == "currency"
        assert infer_column_type("tax_amount", [500.0]) == "currency"
        assert infer_column_type("cgst", [90.0]) == "currency"
        assert infer_column_type("party_unpaid_balance", [4500.0]) == "currency"
        assert infer_column_type("price", [Decimal("123.45")]) == "currency"

        # 3. Number detection (pure number keywords without currency substring)
        assert infer_column_type("quantity", [10]) == "number"
        assert infer_column_type("percentage", [18.5]) == "number"
        assert infer_column_type("row_count", [50]) == "number"
        assert infer_column_type("item_count", [3]) == "number"

        # 4. Numeric timestamps (numbers without date format)
        assert infer_column_type("timestamp_epoch", [1714650000]) == "number"

    def test_decimal_support_in_formatting(self):
        """Python Decimal types must be cleanly formatted in KPI cards."""
        val = Decimal("1234567.89")
        formatted = format_kpi_value(val, "currency")
        assert formatted == "1,234,567.89 INR"

        val_neg = Decimal("-9876.54")
        formatted_neg = format_kpi_value(val_neg, "currency")
        assert formatted_neg == "-9,876.54 INR"

    def test_extreme_values_and_decimals(self):
        """Very large, very small, and zero values must format predictably."""
        assert format_kpi_value(0.0, "currency") == "0.00 INR"
        assert format_kpi_value(0.00001, "currency") == "0.00 INR"
        assert format_kpi_value(1000000000.0, "currency") == "1,000,000,000.00 INR"
        assert format_kpi_value(None, "currency") == "0.00"

    def test_visualization_derivation_llm_suggestions(self):
        """Check LLM suggestion handling: valid types and unknown fallbacks."""
        cols = ["category", "sales"]
        data = [
            {"category": "Produce", "sales": 1000.0},
            {"category": "Dairy", "sales": 2000.0},
        ]
        col_defs = derive_column_defs(cols, data)

        # 1. LLM requests table -> table
        assert derive_suggested_visualization(cols, col_defs, data, "table") == "table"

        # 2. LLM requests invalid visualization -> graceful fallback to valid VisualizationType
        invalid_viz = derive_suggested_visualization(cols, col_defs, data, "unknown_3d_scatter")
        assert invalid_viz in ("table", "bar_chart", "line_chart", "donut_chart", "kpi_grid")

        # 3. Wide table (>=4 columns) prefers table
        wide_cols = ["col1", "col2", "col3", "col4"]
        wide_defs = [ColumnDef(key=c, label=c, type="string") for c in wide_cols]
        assert derive_suggested_visualization(wide_cols, wide_defs, [{"col1": 1}], "bar_chart") == "table"

    def test_column_label_formatting_acronyms_and_cases(self):
        """Verify acronyms and snake/camel case formatting in column labels."""
        assert format_column_label("cogs") == "COGS"
        assert format_column_label("gst_amount") == "GST Amount"
        assert format_column_label("cgst") == "CGST"
        assert format_column_label("party_unpaid_balance") == "Party Unpaid Balance"
        assert format_column_label("voucherDate") == "Voucher Date"
        assert format_column_label("id") == "ID"
        assert format_column_label("sku_code") == "SKU Code"


# =====================================================================
# Test Class 3: Empirical Bug Demonstrations in Milestone 3
# =====================================================================

class TestMilestone3EmpiricalBugs:
    """Verifications of resolved heuristic collisions and edge cases for Milestone 4."""

    def test_bug_1_count_and_quantity_misclassified_as_currency_due_to_substring_match(self):
        """BUG #1 RESOLVED: 'sales_invoice_count' and 'total_units_sold' are classified as 'number'."""
        col_type_invoice_count = infer_column_type("sales_invoice_count", [3])
        col_type_units_sold = infer_column_type("total_units_sold", [7.0])

        assert col_type_invoice_count == "number", "sales_invoice_count correctly classified as number"
        assert col_type_units_sold == "number", "total_units_sold correctly classified as number"

        cards = derive_kpi_cards(
            [
                ColumnDef(key="sales_invoice_count", label="Sales Invoice Count", type=col_type_invoice_count),
                ColumnDef(key="total_units_sold", label="Total Units Sold", type=col_type_units_sold),
            ],
            [{"sales_invoice_count": 3, "total_units_sold": 7.0}],
        )
        card_map = {c.label: c for c in cards}
        assert card_map["Sales Invoice Count"].value == 3
        assert card_map["Total Units Sold"].value == 7.0

    def test_bug_2_none_llm_suggestion_short_circuits_dynamic_chart_inference(self):
        """BUG #2 RESOLVED: llm_suggestion=None on time series derives 'line_chart'."""
        months = [f"2026-{i:02d}-01" for i in range(1, 13)]
        data = [{"month": m, "sales": 1000.0 * i} for i, m in enumerate(months, 1)]
        cols = ["month", "sales"]
        col_defs = derive_column_defs(cols, data)

        actual_viz = derive_suggested_visualization(cols, col_defs, data, llm_suggestion=None)
        assert actual_viz == "line_chart", "12-month time series correctly derives line_chart"

    def test_bug_3_explicit_llm_bar_chart_overridden_by_donut_chart(self):
        """BUG #3 RESOLVED: Explicit model suggestion 'bar_chart' is respected."""
        cols = ["product_category", "sales_amount"]
        data = [
            {"product_category": "Cat 1", "sales_amount": 100.0},
            {"product_category": "Cat 2", "sales_amount": 200.0},
            {"product_category": "Cat 3", "sales_amount": 300.0},
            {"product_category": "Cat 4", "sales_amount": 400.0},
        ]
        col_defs = derive_column_defs(cols, data)

        actual_viz = derive_suggested_visualization(cols, col_defs, data, llm_suggestion="bar_chart")
        assert actual_viz == "bar_chart", "Explicit bar_chart model recommendation is respected"


# =====================================================================
# Test Class 4: End-to-End Adversarial Query Executions
# =====================================================================

class TestEndToEndAdversarialExecutions:
    """End-to-end integration tests using FastAPI TestClient with adversarial payloads."""

    def test_query_returning_negative_net_profit_end_to_end(self, client: TestClient):
        """End-to-end query producing negative net profit returns valid envelope with negative trend."""
        mock_plan = ReportPlanSchema(
            thought_process="Calculate net profit (which is negative in test ledger).",
            tables_used=["voucher", "voucherdetails"],
            metrics_applied=["net_profit"],
            sql_query="SELECT -43232.0 AS net_profit",
            suggested_visualization="kpi_card",
        )
        mock_result = ExecutionResult(
            columns=["net_profit"],
            rows=[{"net_profit": -43232.0}],
            execution_time_ms=8.0,
            row_count=1,
            dialect="sqlite",
        )

        with (
            patch("app.auth.jwt_handler.is_dev_mode", return_value=True),
            patch("app.api.v1.reports.LLMClient.generate_report_plan_async", new_callable=AsyncMock, return_value=mock_plan),
            patch("app.api.v1.reports.DatabaseExecutor.execute_query", return_value=mock_result),
        ):
            resp = client.post(
                "/api/v1/reports/query",
                json={"user_query": "What is our net profit?"},
            )
            assert resp.status_code == 200
            body = resp.json()

            # Envelope contract
            assert body["status"] == "success"
            assert body["error"] is None
            assert body["data"] == [{"net_profit": -43232.0}]

            # Dynamic UI Schema checks
            ui = body["ui_schema"]
            assert ui["suggested_visualization"] == "kpi_grid"
            assert len(ui["kpis"]) == 1
            card = ui["kpis"][0]
            assert card["label"] == "Net Profit"
            assert card["value"] == "-43,232.00 INR"
            assert card["trend"] == "negative"

    def test_query_returning_empty_result_end_to_end(self, client: TestClient):
        """End-to-end query returning 0 rows returns 200 with table visualization and empty data."""
        mock_plan = ReportPlanSchema(
            thought_process="Query vouchers with amount > 10,000,000 (none exist).",
            tables_used=["voucher"],
            metrics_applied=[],
            sql_query="SELECT PartyId, TotalAmount FROM voucher WHERE TotalAmount > 10000000",
            suggested_visualization="table",
        )
        mock_result = ExecutionResult(
            columns=["PartyId", "TotalAmount"],
            rows=[],
            execution_time_ms=4.0,
            row_count=0,
            dialect="sqlite",
        )

        with (
            patch("app.auth.jwt_handler.is_dev_mode", return_value=True),
            patch("app.api.v1.reports.LLMClient.generate_report_plan_async", new_callable=AsyncMock, return_value=mock_plan),
            patch("app.api.v1.reports.DatabaseExecutor.execute_query", return_value=mock_result),
        ):
            resp = client.post(
                "/api/v1/reports/query",
                json={"user_query": "List all vouchers over 10 million"},
            )
            assert resp.status_code == 200
            body = resp.json()

            assert body["status"] == "success"
            assert body["data"] == []
            assert body["ui_schema"]["suggested_visualization"] == "table"
            assert len(body["ui_schema"]["columns"]) == 2
            assert body["ui_schema"]["kpis"] == []
