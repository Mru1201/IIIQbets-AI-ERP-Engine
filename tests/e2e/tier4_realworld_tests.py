"""Tier 4 E2E Real-World Shree Shashwatraj Agro ERP Business Scenario Tests.

Covers:
1. Full P&L Waterfall Reconciliation (Sales -> COGS -> Gross Profit -> Expenses -> Net Profit).
2. Kacha (stock transfer / KACHA) vs Pakka (Sales / PAKKA) Sales & Batch Filtering.
3. Sundry Debtor Unpaid Balance Aging, Credit Limits & Dual-Account Parties.
4. Live Batch Stock Valuation, Negative Stock Detection & Inward/Outward Reconciliation.
5. GST & Tax Inclusive Reporting (CGST, SGST, IGST, HSN, Product GST Rates).
6. Multi-Hop SubSchemaRouter + AST Guardrail + DatabaseExecutor Enterprise Workflows.
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient
import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent
ORCHESTRATOR_ROOT = WORKSPACE_ROOT / "erp-ai-orchestrator"
PIPELINE_ROOT = WORKSPACE_ROOT / "erp-ai-model-pipeline"

for p in (str(ORCHESTRATOR_ROOT), str(PIPELINE_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.api.v1.reports import get_db_executor, get_llm_client
from app.config import DEFAULT_METASTORE_PATH, DEFAULT_SQL_DUMP_PATH
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
# 1. P&L Waterfall Verification (Shree Shashwatraj Agro Pvt Ltd)
# =========================================================================== #
class TestRealWorldPnLWaterfall:
    DATE_RANGE = {"from_date": "2020-01-01", "to_date": "2030-12-31"}

    def test_rw01_total_sales_revenue_reconciliation(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = metastore["calculated_metrics"]["total_sales_revenue"]["canonical_sql"].format(**self.DATE_RANGE)
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        assert res.rows[0]["total_sales_revenue"] == 10368.0
        assert res.rows[0]["sales_invoice_count"] == 3

    def test_rw01_cost_of_goods_sold_reconciliation(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = metastore["calculated_metrics"]["cost_of_goods_sold"]["canonical_sql"].format(**self.DATE_RANGE)
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        assert res.rows[0]["cost_of_goods_sold"] == 7900.0
        assert res.rows[0]["total_units_sold"] == 7.0

    def test_rw01_gross_profit_reconciliation(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = metastore["calculated_metrics"]["gross_profit"]["canonical_sql"].format(**self.DATE_RANGE)
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        row = res.rows[0]
        assert row["total_sales_revenue"] - row["cost_of_goods_sold"] == 2468.0
        assert row["gross_profit"] == 2468.0

    def test_rw01_total_expenses_reconciliation(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = metastore["calculated_metrics"]["total_expenses"]["canonical_sql"].format(**self.DATE_RANGE)
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        assert res.rows[0]["total_expenses"] == 45700.0
        assert res.rows[0]["expense_count"] == 5

    def test_rw01_net_profit_waterfall_identity(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = metastore["calculated_metrics"]["net_profit"]["canonical_sql"].format(**self.DATE_RANGE)
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        row = res.rows[0]
        assert row["total_sales_revenue"] == 10368.0
        assert row["cost_of_goods_sold"] == 7900.0
        assert row["gross_profit"] == 2468.0
        assert row["total_expenses"] == 45700.0
        assert row["net_profit"] == -43232.0
        assert row["gross_profit"] - row["total_expenses"] == row["net_profit"]

    def test_rw01_full_api_pnl_query_with_kpi_cards(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = metastore["calculated_metrics"]["net_profit"]["canonical_sql"].format(**self.DATE_RANGE)
        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.last_token_usage = {"prompt_tokens": 200, "completion_tokens": 80, "total_tokens": 280}
        mock_llm.generate_report_plan_async = AsyncMock(
            return_value=ReportPlanSchema(
                thought_process="Computed full P&L waterfall using canonical CTEs.",
                tables_used=["voucher", "voucherdetails", "products", "expensive"],
                metrics_applied=["total_sales_revenue", "cost_of_goods_sold", "gross_profit", "total_expenses", "net_profit"],
                sql_query=sql,
                suggested_visualization="kpi_card",
            )
        )
        app.dependency_overrides[get_llm_client] = lambda: mock_llm
        app.dependency_overrides[get_db_executor] = lambda: sqlite_executor
        try:
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/reports/query",
                    json={"user_query": "What is our net profit and gross profit for 2026?"},
                )
                assert resp.status_code == 200
                body = resp.json()
                assert body["status"] == "success"
                assert body["ui_schema"]["suggested_visualization"] == "kpi_grid"
                assert body["data"][0]["net_profit"] == -43232.0
                assert body["data"][0]["gross_profit"] == 2468.0
        finally:
            app.dependency_overrides.clear()

    def test_rw01_empty_fy_pnl_returns_zeroes_without_null_errors(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = metastore["calculated_metrics"]["net_profit"]["canonical_sql"].format(
            from_date="2018-04-01", to_date="2019-03-31"
        )
        res = sqlite_executor.execute_query(sql)
        row = res.rows[0]
        assert row["total_sales_revenue"] == 0.0
        assert row["cost_of_goods_sold"] == 0.0
        assert row["gross_profit"] == 0.0
        assert row["total_expenses"] == 0.0
        assert row["net_profit"] == 0.0


# =========================================================================== #
# 2. Kacha vs Pakka Sales & Batch Classification
# =========================================================================== #
class TestRealWorldKachaVsPakka:
    def test_rw02_pakka_sales_vs_purchase_vouchers_breakdown(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = (
            "SELECT TransactionType, COUNT(*) AS voucher_count, SUM(TotalAmount) AS total_val "
            "FROM voucher GROUP BY TransactionType ORDER BY total_val DESC"
        )
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        by_type = {r["TransactionType"]: r for r in res.rows}
        assert by_type["Purchase"]["total_val"] == 11045.0
        assert by_type["Sales"]["total_val"] == 10368.0

    def test_rw02_batch_kacha_vs_pakka_product_type_distribution(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = (
            "SELECT product_type, COUNT(*) AS batch_count, "
            "SUM(CAST(quantity AS DECIMAL(15,2))) AS total_qty "
            "FROM batches GROUP BY product_type ORDER BY product_type"
        )
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        types = {r["product_type"] for r in res.rows}
        assert "PAKKA" in types or "KACHA" in types

    def test_rw02_pakka_batches_valuation(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = (
            "SELECT b.batch_number, b.product_type, CAST(b.quantity AS DECIMAL(15,2)) AS qty, "
            "CAST(b.selling_price AS DECIMAL(15,2)) AS sell_price "
            "FROM batches b WHERE b.product_type = 'PAKKA'"
        )
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        assert res.row_count >= 1
        assert all(r["product_type"] == "PAKKA" for r in res.rows)

    def test_rw02_kacha_batches_valuation(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = (
            "SELECT b.batch_number, b.product_type, CAST(b.quantity AS DECIMAL(15,2)) AS qty "
            "FROM batches b WHERE b.product_type = 'KACHA'"
        )
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        assert all(r["product_type"] == "KACHA" for r in res.rows)

    def test_rw02_saletypes_master_table_inspection(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = "SELECT * FROM saletypes"
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        assert isinstance(res.rows, list)


# =========================================================================== #
# 3. Sundry Debtor Unpaid Balance Aging & Party Ledger Analysis
# =========================================================================== #
class TestRealWorldDebtorAgingAndParties:
    def test_rw03_top_debtors_unpaid_balance_ranking(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = metastore["calculated_metrics"]["party_unpaid_balance"]["canonical_sql"]
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        assert res.row_count == 6
        assert res.rows[0]["name"] == "BABA BARFANI"
        assert res.rows[0]["party_unpaid_balance"] == 39802.09
        assert res.rows[1]["name"] == "Bharath"
        assert res.rows[1]["party_unpaid_balance"] == 24006.74
        # Ensure descending sort order
        balances = [r["party_unpaid_balance"] for r in res.rows]
        assert balances == sorted(balances, reverse=True)

    def test_rw03_party_distribution_by_role(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = "SELECT role, COUNT(*) AS party_count FROM accounts GROUP BY role ORDER BY party_count DESC"
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        total_parties = sum(r["party_count"] for r in res.rows)
        assert total_parties == 55

    def test_rw03_accounts_by_ledger_group_backquoted(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = "SELECT `group`, COUNT(*) AS cnt FROM accounts WHERE `group` IS NOT NULL GROUP BY `group` ORDER BY cnt DESC"
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        assert res.row_count >= 5

    def test_rw03_invoice_level_outstanding_balance_by_party(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = (
            "SELECT PartyName, SUM(balance_amount) AS outstanding_invoice_balance "
            "FROM voucher WHERE TransactionType = 'Sales' GROUP BY PartyName ORDER BY outstanding_invoice_balance DESC"
        )
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        assert res.row_count >= 1

    def test_rw03_dual_account_suppliers_who_are_also_retailers(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = "SELECT id, name, role, is_dual_account FROM accounts WHERE is_dual_account = 1"
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        assert all(r["is_dual_account"] == 1 for r in res.rows)

    def test_rw03_Sensitive_pii_columns_never_exposed_in_select_star_on_accounts(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sanitized = validate_and_sanitize_sql("SELECT * FROM accounts LIMIT 3", metastore)
        res = sqlite_executor.execute_query(sanitized)
        assert res.row_count == 3
        for forbidden_col in ("password", "otp", "aadhaar_number", "bank_account_number", "upi_id"):
            assert forbidden_col not in res.columns


# =========================================================================== #
# 4. Batch Stock Valuation, Negative Stock & Inward/Outward Reconciliation
# =========================================================================== #
class TestRealWorldBatchStockValuation:
    def test_rw04_canonical_batch_stock_on_hand_and_value(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = metastore["calculated_metrics"]["batch_stock_on_hand"]["canonical_sql"]
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        assert res.row_count == 14
        for row in res.rows:
            assert "goods_name" in row
            assert "batch_number" in row
            assert "stock_on_hand" in row
            assert "stock_value" in row

    def test_rw04_negative_stock_anomaly_detection(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = (
            "SELECT p.goods_name, b.batch_number, CAST(b.quantity AS DECIMAL(15,2)) AS stock_on_hand "
            "FROM batches b JOIN products p ON p.id = b.product_id "
            "WHERE CAST(b.quantity AS DECIMAL(15,2)) < 0"
        )
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        assert res.row_count >= 1
        assert any(r["goods_name"] == "Phelan Sherman" and r["stock_on_hand"] == -5.0 for r in res.rows)

    def test_rw04_stock_in_vs_stock_out_movement_analysis(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = (
            "SELECT batch_number, "
            "CAST(opening_stock AS DECIMAL(15,2)) AS open_qty, "
            "CAST(stock_in AS DECIMAL(15,2)) AS in_qty, "
            "CAST(stock_out AS DECIMAL(15,2)) AS out_qty, "
            "CAST(quantity AS DECIMAL(15,2)) AS curr_qty "
            "FROM batches ORDER BY curr_qty DESC"
        )
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        assert res.row_count == 15

    def test_rw04_batch_margin_spread_selling_minus_purchase_price(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = (
            "SELECT p.goods_name, b.batch_number, "
            "CAST(b.selling_price AS DECIMAL(15,2)) - CAST(b.purchase_price AS DECIMAL(15,2)) AS unit_margin "
            "FROM batches b JOIN products p ON p.id = b.product_id"
        )
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        assert res.row_count == 14

    def test_rw04_batch_expiry_date_parsing_via_str_to_date(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = (
            "SELECT batch_number, STR_TO_DATE(exp_date, '%Y-%m-%d') AS parsed_exp "
            "FROM batches WHERE exp_date IS NOT NULL"
        )
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        assert res.row_count >= 1


# =========================================================================== #
# 5. GST & Tax Inclusive Reporting
# =========================================================================== #
class TestRealWorldGSTAndTaxReporting:
    def test_rw05_voucher_gst_tax_and_subtotal_breakdown(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = (
            "SELECT TransactionType, SUM(Subtotal) AS total_taxable, "
            "SUM(TaxAmount) AS total_tax, SUM(TotalAmount) AS grand_total "
            "FROM voucher GROUP BY TransactionType"
        )
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        assert res.row_count == 2

    def test_rw05_voucherdetails_cgst_sgst_igst_line_items(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = (
            "SELECT product, quantity, price, cgst, sgst, igst, total "
            "FROM voucherdetails WHERE transaction_type = 'Sales'"
        )
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        assert res.row_count >= 1

    def test_rw05_products_gst_rate_and_inclusive_gst_status(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = (
            "SELECT goods_name, hsn_code, CAST(gst_rate AS DECIMAL(15,2)) AS gst_pct, "
            "inclusive_gst, CAST(price AS DECIMAL(15,2)) AS base_price "
            "FROM products ORDER BY goods_name"
        )
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        assert res.row_count == 15

    def test_rw05_gstin_registered_parties_list(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = "SELECT id, name, business_name, gstin FROM accounts WHERE gstin IS NOT NULL AND gstin != ''"
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        assert isinstance(res.rows, list)

    def test_rw05_sales_voucher_dc_and_invoice_numbers(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = (
            "SELECT VoucherID, InvoiceNumber, PartyName, TotalAmount, status "
            "FROM voucher WHERE TransactionType = 'Sales' ORDER BY VoucherID"
        )
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        assert res.row_count == 3


# =========================================================================== #
# 6. Multi-Hop Router & Complex Enterprise Analytical Queries
# =========================================================================== #
class TestRealWorldMultiHopAndEnterpriseQueries:
    def test_rw06_four_table_inventory_catalog_join(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = (
            "SELECT p.goods_name, c.category_name, co.company_name, p.unit, "
            "CAST(b.quantity AS DECIMAL(15,2)) AS batch_qty "
            "FROM batches b "
            "JOIN products p ON p.id = b.product_id "
            "LEFT JOIN categories c ON c.id = p.category_id "
            "LEFT JOIN companies co ON co.id = p.company_id "
            "ORDER BY p.goods_name"
        )
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        assert res.row_count == 14

    def test_rw06_router_prunes_and_preserves_multi_hop_join_tables(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        router = SubSchemaRouter(metastore=metastore, force_pure_python=True)
        pruned, meta = router.route_and_prune(
            "Show batch stock quantity with product goods_name and company_name",
            force_prune=True,
            top_k=5,
            max_k=10,
        )
        assert meta["bypassed"] is False
        assert {"batches", "products", "companies"}.issubset(set(pruned["tables"].keys()))
        sql = (
            "SELECT p.goods_name, co.company_name, CAST(b.quantity AS DECIMAL(15,2)) AS qty "
            "FROM batches b JOIN products p ON p.id = b.product_id "
            "LEFT JOIN companies co ON co.id = p.company_id"
        )
        sanitized = validate_and_sanitize_sql(sql, pruned)
        res = sqlite_executor.execute_query(sanitized)
        assert res.row_count == 14

    def test_rw06_bank_direct_deposits_reconciliation(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = (
            "SELECT dd.id, dd.credit, dd.debit, dd.balance, dd.txn_date, a.name AS linked_party "
            "FROM direct_deposit dd LEFT JOIN accounts a ON a.id = dd.party_id "
            "ORDER BY dd.id"
        )
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        assert res.row_count == 20

    def test_rw06_staff_operating_expenses_with_party_join(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = (
            "SELECT e.category, e.amount, e.`date`, e.payment_status, a.name AS staff_name "
            "FROM expensive e LEFT JOIN accounts a ON a.id = e.staff_id "
            "ORDER BY e.amount DESC"
        )
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        assert res.row_count == 5
        assert sum(r["amount"] for r in res.rows) == 45700.0

    def test_rw06_category_discount_history_and_offers_inspection(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql1 = validate_and_sanitize_sql("SELECT * FROM categories_discount_history", metastore)
        sql2 = validate_and_sanitize_sql("SELECT * FROM offers", metastore)
        res1 = sqlite_executor.execute_query(sql1)
        res2 = sqlite_executor.execute_query(sql2)
        assert res1.row_count >= 0
        assert res2.row_count >= 0

    def test_rw06_company_info_and_units_master_tables(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql_ci = validate_and_sanitize_sql("SELECT * FROM company_info", metastore)
        sql_u = validate_and_sanitize_sql("SELECT * FROM units", metastore)
        res_ci = sqlite_executor.execute_query(sql_ci)
        res_u = sqlite_executor.execute_query(sql_u)
        assert res_ci.row_count >= 0
        assert res_u.row_count >= 1

    def test_rw06_voucher_and_voucherdetails_unit_join(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = (
            "SELECT v.VoucherID, v.PartyName, vd.product, vd.quantity, u.name AS unit_name "
            "FROM voucherdetails vd "
            "JOIN voucher v ON v.VoucherID = vd.voucher_id "
            "LEFT JOIN units u ON u.id = vd.unit_id "
            "ORDER BY v.VoucherID"
        )
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        assert res.row_count == 5

    def test_rw06_voucherdetails_batch_join_reconciliation(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = (
            "SELECT vd.product, vd.batch, vd.quantity, CAST(b.quantity AS DECIMAL(15,2)) AS current_batch_stock "
            "FROM voucherdetails vd LEFT JOIN batches b ON b.id = vd.batch_id"
        )
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        assert res.row_count == 5

    def test_rw06_accounts_opening_balance_dr_cr_summary(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = (
            "SELECT balance_type, COUNT(*) AS party_count, COALESCE(SUM(balance), 0) AS total_bal "
            "FROM accounts GROUP BY balance_type"
        )
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        assert res.row_count >= 1

    def test_rw06_sales_visits_and_cart_items_empty_tables_safe(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql_sv = validate_and_sanitize_sql("SELECT * FROM sales_visits", metastore)
        sql_ci = validate_and_sanitize_sql("SELECT * FROM cart_items", metastore)
        assert sqlite_executor.execute_query(sql_sv).row_count >= 0
        assert sqlite_executor.execute_query(sql_ci).row_count >= 0

    def test_rw06_orders_and_order_items_join_safe_on_zero_rows(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = (
            "SELECT o.id, o.order_number, o.net_payable, oi.item_name, oi.quantity "
            "FROM orders o JOIN order_items oi ON oi.order_number = o.order_number"
        )
        sanitized = validate_and_sanitize_sql(sql, metastore)
        res = sqlite_executor.execute_query(sanitized)
        assert res.row_count == 0

    def test_rw06_full_stack_self_correction_on_real_world_pnl_query(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        valid_sql = metastore["calculated_metrics"]["gross_profit"]["canonical_sql"].format(
            from_date="2020-01-01", to_date="2030-12-31"
        )
        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.last_token_usage = {"prompt_tokens": 120, "completion_tokens": 60, "total_tokens": 180}
        mock_llm.generate_report_plan_async = AsyncMock(
            side_effect=[
                ReportPlanSchema(
                    thought_process="Attempt 1 using hallucinated revenue_amount column",
                    tables_used=["voucher"],
                    metrics_applied=["gross_profit"],
                    sql_query="SELECT SUM(revenue_amount) FROM voucher",
                    suggested_visualization="kpi_card",
                ),
                ReportPlanSchema(
                    thought_process="Attempt 2 corrected to canonical gross_profit CTE",
                    tables_used=["voucher", "voucherdetails", "products"],
                    metrics_applied=["gross_profit"],
                    sql_query=valid_sql,
                    suggested_visualization="kpi_card",
                ),
            ]
        )
        app.dependency_overrides[get_llm_client] = lambda: mock_llm
        app.dependency_overrides[get_db_executor] = lambda: sqlite_executor
        try:
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/reports/query",
                    json={"user_query": "Compute our gross profit with COGS breakdown"},
                )
                assert resp.status_code == 200
                body = resp.json()
                assert body["status"] == "success"
                assert body["execution_metadata"]["retries_used"] == 1
                assert body["data"][0]["gross_profit"] == 2468.0
        finally:
            app.dependency_overrides.clear()

