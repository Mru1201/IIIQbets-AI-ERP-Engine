"""Verification tests for Schema Introspector, Semantic Metastore, and ReportPlanSchema."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import DEFAULT_SQL_DUMP_PATH
from app.connectors.introspector import SQLDumpIntrospector, build_metastore, introspect
from app.metastore.loader import MetastoreError, column_index, load_metastore, prune_metastore
from app.schemas.report_plan import ReportPlanSchema, report_plan_json_schema
from app.security.guardrails import validate_and_sanitize_sql


@pytest.fixture(scope="module")
def dump_path() -> Path:
    return DEFAULT_SQL_DUMP_PATH


@pytest.fixture(scope="module")
def metastore() -> dict:
    return load_metastore()


# =========================================================================== #
# 1. SQL DUMP INTROSPECTION & PARSING
# =========================================================================== #

def test_dump_introspector_parses_all_tables(dump_path: Path):
    """Verify that SQLDumpIntrospector parses all 23 tables from the dump file."""
    assert dump_path.exists(), f"Dump file not found: {dump_path}"
    parser = SQLDumpIntrospector(dump_path, database="ledger")
    raw = parser.introspect(profile=True)
    
    assert raw.database == "ledger"
    assert raw.source == "sql_dump"
    assert len(raw.tables) == 23

    # Check key primary keys
    assert raw.tables["voucher"].primary_key == ["VoucherID"]
    assert raw.tables["accounts"].primary_key == ["id"]
    assert raw.tables["accountgroup"].primary_key == ["accountgroup_id"]
    assert raw.tables["batches"].primary_key == ["id"]
    assert raw.tables["expensive"].primary_key == []  # No PK in ground truth

    # Check the 5 declared foreign keys from the SQL dump
    all_fks = []
    for t in raw.tables.values():
        for fk in t.foreign_keys:
            all_fks.append((t.name, fk.columns, fk.ref_table, fk.ref_columns))
    
    assert len(all_fks) == 5
    assert ("batches", ["product_id"], "products", ["id"]) in all_fks
    assert ("categories_discount_history", ["category_id"], "categories", ["id"]) in all_fks
    assert ("orders", ["customer_id"], "accounts", ["id"]) in all_fks
    assert ("orders", ["order_placed_by"], "accounts", ["id"]) in all_fks
    assert ("voucherdetails", ["voucher_id"], "voucher", ["VoucherID"]) in all_fks


# =========================================================================== #
# 2. METASTORE COMPLETENESS & SECURITY EXCLUSIONS
# =========================================================================== #

def test_metastore_metadata_and_security_envelope(metastore: dict):
    """Verify tenant metadata and strict security configuration."""
    assert metastore["tenant_id"] == "shree_shashwatraj_agro"
    assert metastore["tenant_name"] == "Shree Shashwatraj Agro Pvt Ltd"
    assert metastore["sql_dialect"] == "mysql"
    assert metastore["database"] == "ledger"

    sec = metastore["security"]
    assert sec["read_only"] is True
    assert sec["max_row_limit"] == 1000

    # 2 restricted credential tables + 1 internal audit log excluded
    assert "gsp_credentials" in sec["restricted_tables"]
    assert "gsp_api" in sec["restricted_tables"]
    assert "gsp_credentials" in sec["excluded_tables"]
    assert "gsp_api" in sec["excluded_tables"]
    assert "chatbot_audit_log" in sec["excluded_tables"]

    # 20 exposed tables (23 - 3 excluded)
    assert len(metastore["tables"]) == 20
    assert "gsp_credentials" not in metastore["tables"]
    assert "gsp_api" not in metastore["tables"]
    assert "chatbot_audit_log" not in metastore["tables"]


def test_sensitive_columns_excluded_from_metastore(metastore: dict):
    """Verify credentials, tokens, PII, and large blobs are stripped."""
    accounts_cols = metastore["tables"]["accounts"]["columns"]
    for forbidden in ("password", "otp", "otp_expires_at", "otp_used", "aadhaar_number", "bank_account_number", "upi_id"):
        assert forbidden not in accounts_cols

    voucher_cols = metastore["tables"]["voucher"]["columns"]
    assert "pdf_data" not in voucher_cols


# =========================================================================== #
# 3. MODULE SCOPES & CATEGORICAL VALUES
# =========================================================================== #

def test_module_scopes_assigned(metastore: dict):
    """Verify all 20 tables belong to valid module scopes."""
    expected_modules = {"accounting", "inventory", "sales_purchase", "orders", "finance_expenses"}
    assert set(metastore["modules"].keys()) == expected_modules

    for tname, tdata in metastore["tables"].items():
        scopes = tdata.get("module_scope", [])
        assert len(scopes) >= 1, f"Table {tname} has no module scope"
        for s in scopes:
            assert s in expected_modules, f"Table {tname} has invalid scope {s}"


def test_categorical_values_profiled(metastore: dict):
    """Verify enum and categorical known values are captured in the metastore."""
    # voucher.TransactionType
    v_type = metastore["tables"]["voucher"]["columns"]["TransactionType"]
    assert "Sales" in v_type.get("known_values", [])
    assert "Purchase" in v_type.get("known_values", [])

    # expensive.payment_status
    exp_status = metastore["tables"]["expensive"]["columns"]["payment_status"]
    assert "unpaid" in exp_status.get("known_values", [])

    # batches.product_type
    b_type = metastore["tables"]["batches"]["columns"]["product_type"]
    assert "PAKKA" in b_type.get("known_values", [])


# =========================================================================== #
# 4. ALLOWED JOINS & LOGICAL BRIDGES
# =========================================================================== #

def test_allowed_joins_completeness(metastore: dict):
    """Verify both declared and unenforced logical joins are registered."""
    joins = metastore["allowed_joins"]
    join_pairs = {(j["left_table"], j["left_column"], j["right_table"], j["right_column"]) for j in joins}

    # Declared FKs
    assert ("batches", "product_id", "products", "id") in join_pairs
    assert ("voucherdetails", "voucher_id", "voucher", "VoucherID") in join_pairs
    assert ("orders", "customer_id", "accounts", "id") in join_pairs

    # Unenforced logical relationships required by task
    assert ("order_items", "order_number", "orders", "order_number") in join_pairs
    assert ("voucher", "PartyID", "accounts", "id") in join_pairs
    assert ("voucher", "AccountID", "accounts", "id") in join_pairs
    assert ("voucherdetails", "product_id", "products", "id") in join_pairs
    assert ("direct_deposit", "voucher_id", "voucher", "VoucherID") in join_pairs
    assert ("direct_deposit", "party_id", "accounts", "id") in join_pairs


# =========================================================================== #
# 5. DATA CAVEATS & VARCHAR NUMERIC ANNOTATIONS
# =========================================================================== #

def test_data_caveats_annotations(metastore: dict):
    """Verify VARCHAR numbers (unpaid_amount, opening_balance) have CAST annotations."""
    accounts = metastore["tables"]["accounts"]["columns"]
    assert accounts["unpaid_amount"]["caveat"] == "NUMERIC_STORED_AS_TEXT"
    assert "CAST" in accounts["unpaid_amount"]["cast_hint"]
    assert accounts["opening_balance"]["caveat"] == "NUMERIC_STORED_AS_TEXT"
    assert "CAST" in accounts["opening_balance"]["cast_hint"]

    batches = metastore["tables"]["batches"]["columns"]
    assert batches["quantity"]["caveat"] == "NUMERIC_STORED_AS_TEXT"
    assert batches["purchase_price"]["caveat"] == "NUMERIC_STORED_AS_TEXT"

    # Caveats list
    caveat_issues = {c.get("issue") for c in metastore["data_caveats"]}
    assert "NUMERIC_STORED_AS_TEXT" in caveat_issues
    assert "DATE_STORED_AS_TEXT" in caveat_issues
    assert "RESERVED_WORD_COLUMNS" in caveat_issues


# =========================================================================== #
# 6. CALCULATED METRICS CODIFICATION & CANONICAL SQL PASS-THROUGH
# =========================================================================== #

def test_calculated_metrics_codification(metastore: dict):
    """Verify all 6 required business formulas are codified and pass AST security."""
    metrics = metastore["calculated_metrics"]
    required_keys = [
        "total_sales_revenue",
        "cost_of_goods_sold",
        "total_expenses",
        "gross_profit",
        "net_profit",
        "party_unpaid_balance",
    ]
    for key in required_keys:
        assert key in metrics, f"Metric {key} is missing from metastore"
        metric = metrics[key]
        assert "description" in metric
        assert "canonical_sql" in metric
        
        # Test formatting date placeholders and validating through security gate
        canon_sql = metric["canonical_sql"].format(
            from_date="2026-04-01",
            to_date="2027-03-31",
        )
        sanitized = validate_and_sanitize_sql(canon_sql, metastore)
        assert "LIMIT 1000" in sanitized


# =========================================================================== #
# 7. PRUNER FUNCTIONALITY
# =========================================================================== #

def test_pruner_keeps_requested_modules(metastore: dict):
    """Verify that pruning to 'inventory' retains relevant tables and drops unrelated ones."""
    pruned = prune_metastore(metastore, modules=["inventory"])
    tables = pruned["tables"]

    # Inventory tables should be present
    assert "batches" in tables
    assert "products" in tables
    assert "categories" in tables
    assert "accounts" in tables  # accounts is always_include

    # Finance/orders only tables should be pruned
    assert "expensive" not in tables
    assert "cart_items" not in tables

    # Joins should only connect retained tables
    for j in pruned["allowed_joins"]:
        assert j["left_table"] in tables
        assert j["right_table"] in tables


def test_pruner_invalid_module_raises(metastore: dict):
    with pytest.raises(MetastoreError):
        prune_metastore(metastore, modules=["non_existent_module"])


# =========================================================================== #
# 8. REPORT PLAN SCHEMA CONTRACT
# =========================================================================== #

def test_report_plan_schema_valid_query():
    """Verify valid ReportPlanSchema instantiation."""
    plan = ReportPlanSchema(
        thought_process="Filtering voucher by Sales",
        tables_used=["voucher"],
        metrics_applied=["total_sales_revenue"],
        sql_query="SELECT COALESCE(SUM(TotalAmount), 0) FROM voucher WHERE TransactionType = 'Sales'",
        suggested_visualization="kpi_card",
    )
    assert plan.is_refusal is False
    assert plan.error is None
    assert plan.sql_query is not None


def test_report_plan_schema_valid_refusal():
    """Verify refusal contract when user requests mutation."""
    plan = ReportPlanSchema(
        thought_process="User requested dropping accounts table.",
        tables_used=[],
        metrics_applied=[],
        sql_query=None,
        error="Refusal: Mutating database schema or data is strictly forbidden.",
        suggested_visualization="table",
    )
    assert plan.is_refusal is True
    assert plan.sql_query is None
    assert "forbidden" in plan.error


def test_report_plan_schema_invariants():
    """Verify mutual exclusivity between sql_query and error."""
    # Both provided
    with pytest.raises(ValueError):
        ReportPlanSchema(
            thought_process="Invalid",
            sql_query="SELECT 1",
            error="An error occurred",
        )

    # Neither provided
    with pytest.raises(ValueError):
        ReportPlanSchema(
            thought_process="Invalid",
            sql_query=None,
            error=None,
        )


def test_report_plan_json_schema_strict():
    """Verify strict JSON schema generation for constrained inference."""
    schema = report_plan_json_schema(strict=True)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert "thought_process" in schema["required"]
    assert "sql_query" in schema["required"]
    assert "error" in schema["required"]
    assert "suggested_visualization" in schema["required"]
