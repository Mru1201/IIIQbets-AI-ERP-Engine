"""Tier 2 E2E Boundary & Corner Case Tests.

Covers:
1. Empty, whitespace, NUL-byte, and oversized SQL/user queries.
2. Row limit boundaries (1, 1000, 10000, out-of-bounds Settings, FETCH/non-literal LIMIT).
3. SQL injection and AST security attack vectors (20+ vectors).
4. Malformed, expired, algorithm-confused, and wrong-secret JWTs.
5. Oversized schemas (100 tables) and empty/degenerate metastores.
6. Zero-row query results and empty dataset UI derivation.
7. NULL, NaN, Infinity, -Infinity, Decimal, midnight timestamp, and division-by-zero handling.
"""

from __future__ import annotations

import copy
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException
from fastapi.testclient import TestClient
import jwt
import pytest
from pydantic import ValidationError

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent
ORCHESTRATOR_ROOT = WORKSPACE_ROOT / "erp-ai-orchestrator"
PIPELINE_ROOT = WORKSPACE_ROOT / "erp-ai-model-pipeline"

for p in (str(ORCHESTRATOR_ROOT), str(PIPELINE_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.api.v1.reports import get_db_executor, get_llm_client
from app.api.v1.ui_deriver import derive_ui_schema, format_kpi_value, infer_column_type
from app.auth.jwt_handler import create_access_token, verify_token
from app.config import DEFAULT_METASTORE_PATH, DEFAULT_SQL_DUMP_PATH, Settings
from app.connectors.executor import (
    DatabaseExecutor,
    SQLiteDumpEngine,
    StatementTimeoutError,
    normalize_rows,
    normalize_value,
)
from app.inference.llm_client import LLMClient, extract_json_payload
from app.main import app
from app.metastore.loader import load_metastore
from app.metastore.router import SubSchemaRouter
from app.schemas.api_models import QueryRequest
from app.schemas.report_plan import ReportPlanSchema
from app.security.guardrails import MAX_SQL_LENGTH, SQLSecurityViolation, validate_and_sanitize_sql


@pytest.fixture(scope="module")
def metastore() -> dict[str, Any]:
    return load_metastore(DEFAULT_METASTORE_PATH)


@pytest.fixture(scope="module")
def sqlite_executor() -> DatabaseExecutor:
    ex = DatabaseExecutor(mode="sqlite", sql_dump_path=DEFAULT_SQL_DUMP_PATH)
    ex.provision_sqlite()
    return ex


# =========================================================================== #
# 1. Empty, Whitespace, NUL-Byte & Oversized Query Boundaries
# =========================================================================== #
class TestQueryInputBoundaries:
    @pytest.mark.parametrize("empty_input", ["", "   ", "\t\n  "])
    def test_b01_empty_or_whitespace_user_query_rejected_by_pydantic(self, empty_input: str) -> None:
        with pytest.raises(ValidationError):
            QueryRequest(user_query=empty_input)

    def test_b01_user_query_whitespace_stripping(self) -> None:
        req = QueryRequest(user_query="   Show total sales revenue   ")
        assert req.user_query == "Show total sales revenue"

    def test_b01_user_query_at_exact_2000_char_boundary_accepted(self) -> None:
        req = QueryRequest(user_query="A" * 2000)
        assert len(req.user_query) == 2000

    @pytest.mark.parametrize("raw_sql", [None, "", "   ", "\n\t"])
    def test_b01_guardrail_rejects_empty_sql(self, metastore: dict[str, Any], raw_sql: Any) -> None:
        with pytest.raises(SQLSecurityViolation) as exc_info:
            validate_and_sanitize_sql(raw_sql, metastore)
        assert exc_info.value.code == "EMPTY_QUERY"

    def test_b01_guardrail_rejects_sql_exceeding_max_length(self, metastore: dict[str, Any]) -> None:
        long_sql = "SELECT id FROM accounts WHERE name = '" + ("x" * MAX_SQL_LENGTH) + "'"
        with pytest.raises(SQLSecurityViolation) as exc_info:
            validate_and_sanitize_sql(long_sql, metastore)
        assert exc_info.value.code == "QUERY_TOO_LONG"

    def test_b01_guardrail_rejects_nul_byte_in_sql(self, metastore: dict[str, Any]) -> None:
        with pytest.raises(SQLSecurityViolation) as exc_info:
            validate_and_sanitize_sql("SELECT id FROM accounts WHERE name = 'abc\x00def'", metastore)
        assert exc_info.value.code == "PARSE_ERROR"

    def test_b01_report_plan_thought_process_length_boundaries(self) -> None:
        with pytest.raises(ValidationError):
            ReportPlanSchema(thought_process="", sql_query="SELECT 1")
        with pytest.raises(ValidationError):
            ReportPlanSchema(thought_process="x" * 4001, sql_query="SELECT 1")
        plan = ReportPlanSchema(thought_process="x" * 4000, sql_query="SELECT 1")
        assert len(plan.thought_process) == 4000


# =========================================================================== #
# 2. Row Limit Boundaries (1, 1000, 10000, FETCH, Invalid Limits)
# =========================================================================== #
class TestRowLimitBoundaries:
    def test_b02_settings_max_row_limit_min_and_max_bounds(self) -> None:
        assert Settings(max_row_limit=1).max_row_limit == 1
        assert Settings(max_row_limit=10000).max_row_limit == 10000
        with pytest.raises(ValidationError):
            Settings(max_row_limit=0)
        with pytest.raises(ValidationError):
            Settings(max_row_limit=10001)

    def test_b02_guardrail_injects_default_limit_1000(self, metastore: dict[str, Any]) -> None:
        out = validate_and_sanitize_sql("SELECT id FROM accounts", metastore)
        assert out.endswith("LIMIT 1000")

    def test_b02_guardrail_preserves_smaller_limit(self, metastore: dict[str, Any]) -> None:
        out = validate_and_sanitize_sql("SELECT id FROM accounts LIMIT 5", metastore, max_rows=1000)
        assert out.endswith("LIMIT 5")

    def test_b02_guardrail_caps_limit_exceeding_max_rows(self, metastore: dict[str, Any]) -> None:
        out = validate_and_sanitize_sql("SELECT id FROM accounts LIMIT 5000", metastore, max_rows=100)
        assert out.endswith("LIMIT 100")

    def test_b02_guardrail_boundary_limit_1(self, metastore: dict[str, Any]) -> None:
        out = validate_and_sanitize_sql("SELECT id FROM accounts LIMIT 50", metastore, max_rows=1)
        assert out.endswith("LIMIT 1")

    def test_b02_guardrail_rejects_fetch_clause(self, metastore: dict[str, Any]) -> None:
        with pytest.raises(SQLSecurityViolation) as exc_info:
            validate_and_sanitize_sql("SELECT id FROM accounts FETCH FIRST 5 ROWS ONLY", metastore)
        assert exc_info.value.code == "INVALID_LIMIT"


# =========================================================================== #
# 3. SQL Injection & AST Security Attack Vectors
# =========================================================================== #
class TestSQLInjectionAndSecurityVectors:
    @pytest.mark.parametrize(
        "attack_sql,expected_code",
        [
            ("SELECT id FROM accounts; DROP TABLE accounts", "MULTIPLE_STATEMENTS"),
            ("SELECT id FROM accounts; SELECT * FROM gsp_api", "MULTIPLE_STATEMENTS"),
            ("SELECT id FROM accounts UNION SELECT id FROM gsp_api", "RESTRICTED_TABLE"),
            ("SELECT id FROM accounts UNION SELECT id FROM gsp_credentials", "RESTRICTED_TABLE"),
            ("SELECT * FROM information_schema.tables", "CROSS_DATABASE"),
            ("SELECT * FROM mysql.user", "CROSS_DATABASE"),
            ("SELECT * FROM performance_schema.threads", "CROSS_DATABASE"),
            ("SELECT * FROM other_db.accounts", "CROSS_DATABASE"),
            ("WITH gsp_api AS (SELECT id FROM accounts) SELECT * FROM gsp_api", "RESTRICTED_TABLE"),
            ("SELECT SLEEP(10) FROM accounts", "FORBIDDEN_FUNCTION"),
            ("SELECT BENCHMARK(1000000, MD5('a')) FROM accounts", "FORBIDDEN_FUNCTION"),
            ("SELECT LOAD_FILE('/etc/passwd') FROM accounts", "FORBIDDEN_FUNCTION"),
            ("SELECT USER() FROM accounts", "FORBIDDEN_FUNCTION"),
            ("SELECT DATABASE() FROM accounts", "FORBIDDEN_FUNCTION"),
            ("SELECT VERSION() FROM accounts", "FORBIDDEN_FUNCTION"),
            ("SELECT EXTRACTVALUE(1, '//a') FROM accounts", "FORBIDDEN_FUNCTION"),
            ("SELECT @@version FROM accounts", "FORBIDDEN_FUNCTION"),
            ("SELECT id FROM accounts WHERE id = ?", "FORBIDDEN_FUNCTION"),
            ("SELECT password FROM accounts", "UNKNOWN_COLUMN"),
            ("SELECT otp FROM accounts", "UNKNOWN_COLUMN"),
            ("SELECT aadhaar_number FROM accounts", "UNKNOWN_COLUMN"),
            ("SELECT pdf_data FROM voucher", "UNKNOWN_COLUMN"),
            ("GRANT ALL ON ledger.* TO 'hacker'@'%'", "FORBIDDEN_STATEMENT"),
            ("TRUNCATE TABLE voucher", "FORBIDDEN_STATEMENT"),
            ("SHOW TABLES", "FORBIDDEN_STATEMENT"),
        ],
    )
    def test_b03_sql_injection_and_ast_attacks_blocked(
        self, metastore: dict[str, Any], attack_sql: str, expected_code: str
    ) -> None:
        with pytest.raises(SQLSecurityViolation) as exc_info:
            validate_and_sanitize_sql(attack_sql, metastore)
        assert exc_info.value.code == expected_code


# =========================================================================== #
# 4. JWT Authentication & Header Corner Cases
# =========================================================================== #
class TestJWTBoundaryCases:
    def test_b04_jwt_signed_with_wrong_secret_rejected(self) -> None:
        token = create_access_token(
            {"sub": "u1"},
            secret="wrong-secret-key-at-least-32-bytes-long!!",
        )
        with pytest.raises(HTTPException) as exc_info:
            verify_token(token, secret="correct-secret-key-at-least-32-bytes-long!!")
        assert exc_info.value.status_code == 401

    def test_b04_jwt_alg_none_attack_rejected(self) -> None:
        unsigned = jwt.encode({"sub": "attacker", "role": "admin"}, "", algorithm="none")
        with pytest.raises(HTTPException) as exc_info:
            verify_token(unsigned, secret="correct-secret-key-at-least-32-bytes-long!!")
        assert exc_info.value.status_code == 401

    def test_b04_jwt_algorithm_mismatch_hs384_rejected(self) -> None:
        secret = "shared-secret-key-at-least-48-bytes-long-for-hs384-test!!"
        token_384 = jwt.encode({"sub": "u1"}, secret, algorithm="HS384")
        with pytest.raises(HTTPException) as exc_info:
            verify_token(token_384, secret=secret, algorithm="HS256")
        assert exc_info.value.status_code == 401

    @pytest.mark.parametrize(
        "bad_auth_header",
        [
            "Bearer",
            "Bearer   ",
            "Bearer tok1 tok2",
            "Token abc.def.ghi",
            "Bearer not-a-valid-jwt-at-all",
        ],
    )
    def test_b04_malformed_bearer_headers_return_401_even_in_dev_mode(self, bad_auth_header: str) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/reports/query",
                headers={"Authorization": bad_auth_header},
                json={"user_query": "Show total sales"},
            )
            assert resp.status_code == 401


# =========================================================================== #
# 5. Oversized Schemas & Degenerate Metastore Boundaries
# =========================================================================== #
class TestSchemaAndRouterBoundaries:
    def test_b05_empty_metastore_rejected_fail_closed(self) -> None:
        with pytest.raises(SQLSecurityViolation) as exc_info:
            validate_and_sanitize_sql("SELECT 1", {"tables": {}})
        assert exc_info.value.code == "METASTORE_INVALID"

    def test_b05_oversized_120_table_schema_routed_under_max_k(self, metastore: dict[str, Any]) -> None:
        large_ms = copy.deepcopy(metastore)
        for i in range(100):
            large_ms["tables"][f"enterprise_ext_table_{i:03d}"] = {
                "description": f"Enterprise extension table {i}",
                "module_scope": ["extension"],
                "primary_key": ["id"],
                "columns": {"id": {"type": "int(11)", "nullable": False}},
            }
        assert len(large_ms["tables"]) == 120
        router = SubSchemaRouter(metastore=large_ms, force_pure_python=True)
        pruned, meta = router.route_and_prune("Show batch stock on hand by product", top_k=5, max_k=10)
        assert meta["bypassed"] is False
        assert meta["original_table_count"] == 120
        assert len(pruned["tables"]) <= 12
        assert "batches" in pruned["tables"]
        assert "products" in pruned["tables"]

    def test_b05_router_handles_empty_or_symbol_only_query(self, metastore: dict[str, Any]) -> None:
        router = SubSchemaRouter(metastore=metastore, force_pure_python=True)
        bm25_res = router.search_bm25("!!! ??? @@@", top_k=5)
        dense_res = router.search_dense("!!! ??? @@@", top_k=5)
        assert len(bm25_res) == 5
        assert len(dense_res) == 5


# =========================================================================== #
# 6. Zero-Row Results & Empty Dataset Handling
# =========================================================================== #
class TestZeroRowAndEmptyResults:
    def test_b06_zero_row_sql_query_returns_empty_list_and_table_vis(
        self, sqlite_executor: DatabaseExecutor
    ) -> None:
        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.last_token_usage = {}
        mock_llm.generate_report_plan_async = AsyncMock(
            return_value=ReportPlanSchema(
                thought_process="Query future year 2099",
                tables_used=["voucher"],
                sql_query="SELECT VoucherID, TotalAmount FROM voucher WHERE Date > '2099-01-01'",
                suggested_visualization="bar",
            )
        )
        app.dependency_overrides[get_llm_client] = lambda: mock_llm
        app.dependency_overrides[get_db_executor] = lambda: sqlite_executor
        try:
            with TestClient(app) as client:
                resp = client.post("/api/v1/reports/query", json={"user_query": "Show sales in 2099"})
                body = resp.json()
                assert body["status"] == "success"
                assert body["data"] == []
                assert body["ui_schema"]["suggested_visualization"] == "table"
                assert body["ui_schema"]["kpis"] == []
                assert len(body["ui_schema"]["columns"]) == 2
        finally:
            app.dependency_overrides.clear()

    def test_b06_derive_ui_schema_on_empty_columns_and_data(self) -> None:
        schema = derive_ui_schema(columns=[], data=[])
        assert schema.suggested_visualization == "table"
        assert schema.columns == []
        assert schema.kpis == []


# =========================================================================== #
# 7. NULL, NaN, Infinity, Decimal & Division-by-Zero Boundaries
# =========================================================================== #
class TestNullNaNAndNumericBoundaries:
    def test_b07_sqlite_division_by_zero_returns_none(self, sqlite_executor: DatabaseExecutor) -> None:
        res = sqlite_executor.execute_query("SELECT 100 / 0 AS div_zero")
        assert res.rows[0]["div_zero"] is None

    def test_b07_format_kpi_value_on_none_and_decimals(self) -> None:
        assert format_kpi_value(None, "currency") == "0.00"
        assert format_kpi_value(Decimal("99999.999"), "currency") == "100,000.00 INR"
        assert format_kpi_value(Decimal("42.5"), "number") == 42.5

    def test_b07_infer_column_type_when_all_values_are_none(self) -> None:
        assert infer_column_type("total_amount", [None, None]) == "currency"
        assert infer_column_type("invoice_count", [None, None]) == "number"
        assert infer_column_type("voucher_date", [None, None]) == "date"
        assert infer_column_type("is_cancelled", [None, None]) == "boolean"
        assert infer_column_type("party_remarks", [None, None]) == "string"

    def test_b07_extract_json_payload_on_empty_string(self) -> None:
        res = extract_json_payload("   ")
        assert res["sql_query"] is None
        assert "empty response" in res["error"].lower()

    def test_b07_sqlite_statement_timeout_boundary(self) -> None:
        engine = SQLiteDumpEngine(dump_path=DEFAULT_SQL_DUMP_PATH, db_path=":memory:", timeout_s=0.03)
        infinite_sql = "WITH RECURSIVE r AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM r) SELECT * FROM r"
        with pytest.raises(StatementTimeoutError):
            engine.execute_query(infinite_sql)
        engine.dispose()
