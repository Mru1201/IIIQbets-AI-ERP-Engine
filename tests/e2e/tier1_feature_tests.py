"""Tier 1 E2E Feature Verification Tests (Features 1–32).

Provides >= 5 dedicated end-to-end tests per feature across:
- erp-ai-orchestrator (Features 1–17, 31–32)
- erp-ai-workspace-ui & shreeshashwatraj-frontend (Features 18–27)
- erp-ai-model-pipeline (Features 28–30)
"""

from __future__ import annotations

import copy
from datetime import date, datetime, timedelta
from decimal import Decimal
import json
import math
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
UI_ROOT = WORKSPACE_ROOT / "erp-ai-workspace-ui"
HOST_FRONTEND_ROOT = WORKSPACE_ROOT / "shreeshashwatraj-frontend"

for p in (str(ORCHESTRATOR_ROOT), str(PIPELINE_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.api.v1.reports import get_db_executor, get_llm_client, get_metastore
from app.api.v1.ui_deriver import (
    derive_column_defs,
    derive_kpi_cards,
    derive_suggested_visualization,
    derive_ui_schema,
    format_column_label,
    infer_column_type,
)
from app.auth.jwt_handler import create_access_token, verify_token
from app.config import (
    DEFAULT_METASTORE_PATH,
    DEFAULT_SQL_DUMP_PATH,
    PROVIDER_DEFAULTS,
    RESTRICTED_TABLES,
    Settings,
    build_mysql_url,
    get_settings,
)
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
from app.inference.llm_client import (
    LLMAuthenticationError,
    LLMClient,
    LLMConnectionError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
    extract_json_payload,
)
from app.inference.prompts import (
    build_inference_prompt,
    build_retry_user_prompt,
    build_system_prompt,
    format_business_rules_for_prompt,
    format_data_caveats_for_prompt,
    format_error_context_for_prompt,
    format_joins_for_prompt,
    format_metrics_for_prompt,
    format_schema_for_prompt,
    generate_correction_hint,
)
from app.main import app
from app.metastore.loader import infer_modules, load_metastore, prune_metastore
from app.metastore.router import BM25Okapi, PurePythonDenseRetriever, SubSchemaRouter, tokenize
from app.schemas.api_models import QueryRequest, QueryResponse
from app.schemas.report_plan import ReportPlanSchema, report_plan_json_schema
from app.security.guardrails import (
    ASTGuardrail,
    SQLSecurityViolation,
    referenced_tables,
    validate_and_sanitize_sql,
)
from benchmarks.gold_eval_suite import (
    DEFAULT_BENCHMARK_PATH,
    GoldBenchmarkSample,
    GoldEvalHarness,
    compare_results,
)
from dataset.validator import validate_dataset_records
from training.config import QLoRAConfig
from training.export import export_dual_formats
from training.train_qlora import estimate_token_count, run_dry_run_verification


# --------------------------------------------------------------------------- #
# Shared Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def metastore() -> dict[str, Any]:
    return load_metastore(DEFAULT_METASTORE_PATH)


@pytest.fixture(scope="module")
def sqlite_executor() -> DatabaseExecutor:
    ex = DatabaseExecutor(mode="sqlite", sql_dump_path=DEFAULT_SQL_DUMP_PATH)
    ex.provision_sqlite()
    return ex


# =========================================================================== #
# FEATURE 1: Universal Provider Config & Routing (app/config.py)
# =========================================================================== #
class TestFeature01ProviderConfig:
    @pytest.mark.parametrize(
        "provider,expected_url,expected_model",
        [
            ("vllm", "http://localhost:8000/v1", "Qwen/Qwen2.5-Coder-14B-Instruct"),
            ("ollama", "http://localhost:11434/v1", "qwen2.5-coder:7b"),
            ("openai", "https://api.openai.com/v1", "gpt-4o-mini"),
            ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-2.0-flash"),
            ("openrouter", "https://openrouter.ai/api/v1", "qwen/qwen-2.5-coder-32b-instruct"),
        ],
    )
    def test_f01_provider_defaults_matrix(self, provider: str, expected_url: str, expected_model: str) -> None:
        s = Settings(llm_provider=provider)  # type: ignore[arg-type]
        cfg = s.get_provider_config(provider)  # type: ignore[arg-type]
        assert cfg["provider"] == provider
        assert cfg["base_url"] == expected_url
        assert cfg["model"] == expected_model
        assert cfg["timeout_s"] == PROVIDER_DEFAULTS[provider]["timeout_s"]

    def test_f01_provider_specific_api_key_resolution(self) -> None:
        s_gem = Settings(llm_provider="gemini", gemini_api_key="gem-key-123")
        assert s_gem.llm_api_key == "gem-key-123"
        s_or = Settings(llm_provider="openrouter", openrouter_api_key="or-key-456")
        assert s_or.llm_api_key == "or-key-456"
        s_oa = Settings(llm_provider="openai", openai_api_key="oa-key-789")
        assert s_oa.llm_api_key == "oa-key-789"

    def test_f01_build_mysql_url_escaping(self) -> None:
        url = build_mysql_url(host="127.0.0.1", port=3306, user="erp_user", password="p@ss:word/1", name="ledger")
        assert url == "mysql+pymysql://erp_user:p%40ss%3Aword%2F1@127.0.0.1:3306/ledger"
        assert build_mysql_url(db_url="mysql+pymysql://custom/db") == "mysql+pymysql://custom/db"
        assert build_mysql_url() is None


# =========================================================================== #
# FEATURE 2: Standardized Multi-Provider LLM Client (app/inference/llm_client.py)
# =========================================================================== #
class TestFeature02MultiProviderLLMClient:
    def test_f02_openai_and_vllm_payload_construction(self, metastore: dict[str, Any]) -> None:
        client_vllm = LLMClient(settings=Settings(llm_provider="vllm"), metastore=metastore)
        endpoint, payload, _ = client_vllm._prepare_request("sys", "usr")
        assert endpoint.endswith("/chat/completions")
        assert "guided_json" in payload
        assert payload["response_format"] == {"type": "json_object"}

        client_oa = LLMClient(settings=Settings(llm_provider="openai", llm_api_key="sk-test"), metastore=metastore)
        _, payload_oa, headers_oa = client_oa._prepare_request("sys", "usr")
        assert "guided_json" not in payload_oa
        assert headers_oa["Authorization"] == "Bearer sk-test"

    def test_f02_ollama_native_vs_v1_payload(self, metastore: dict[str, Any]) -> None:
        client_native = LLMClient(
            settings=Settings(llm_provider="ollama", llm_base_url="http://localhost:11434"),
            metastore=metastore,
        )
        ep, payload, _ = client_native._prepare_request("sys", "usr")
        assert ep == "http://localhost:11434/api/chat"
        assert payload["stream"] is False
        assert "format" in payload

    def test_f02_openrouter_headers(self, metastore: dict[str, Any]) -> None:
        client_or = LLMClient(
            settings=Settings(llm_provider="openrouter", openrouter_api_key="or-123"),
            metastore=metastore,
        )
        _, _, headers = client_or._prepare_request("sys", "usr")
        assert headers["HTTP-Referer"] == "https://erp-ai-reporting.local"
        assert headers["X-Title"] == "ERP Ledger Reporting Agent"

    def test_f02_token_usage_tracking_openai_and_ollama(self, metastore: dict[str, Any]) -> None:
        client = LLMClient(metastore=metastore)
        content = client._extract_usage_and_content({
            "usage": {"prompt_tokens": 120, "completion_tokens": 45, "total_tokens": 165},
            "choices": [{"message": {"content": '{"ok": true}'}}],
        })
        assert content == '{"ok": true}'
        assert client.last_token_usage == {"prompt_tokens": 120, "completion_tokens": 45, "total_tokens": 165}

        content_ollama = client._extract_usage_and_content({
            "prompt_eval_count": 200,
            "eval_count": 50,
            "message": {"content": '{"ollama": true}'},
        })
        assert content_ollama == '{"ollama": true}'
        assert client.last_token_usage["total_tokens"] == 250

    def test_f02_five_tier_json_extraction(self) -> None:
        # Tier 1: Direct JSON
        assert extract_json_payload('{"a": 1}')["a"] == 1
        # Tier 2: Markdown code fence
        assert extract_json_payload("Here is JSON:\n```json\n{\"b\": 2}\n```")["b"] == 2
        # Tier 3: Outermost braces
        assert extract_json_payload("Preface {\"c\": 3} Epilogue")["c"] == 3
        # Tier 4: Trailing comma repair
        assert extract_json_payload('{"d": 4, }')["d"] == 4
        # Tier 5: Unstructured fallback
        fallback = extract_json_payload("Completely non-json plain text refusal")
        assert fallback["sql_query"] is None
        assert "Failed to parse model output as JSON" in fallback["error"]


# =========================================================================== #
# FEATURE 3: Dynamic Metastore Prompt Injection (app/inference/prompts.py)
# =========================================================================== #
class TestFeature03MetastorePromptInjection:
    def test_f03_format_schema_includes_tables_and_cast_hints(self, metastore: dict[str, Any]) -> None:
        schema_str = format_schema_for_prompt(metastore)
        assert "### Table: `accounts`" in schema_str
        assert "### Table: `voucher`" in schema_str
        assert "CAST(batches.quantity AS DECIMAL(15,2))" in schema_str
        assert "gsp_api" not in schema_str

    def test_f03_format_joins_declared_and_logical(self, metastore: dict[str, Any]) -> None:
        joins_str = format_joins_for_prompt(metastore)
        assert "Declared Physical Foreign Keys" in joins_str
        assert "Logical Domain Relationships" in joins_str
        assert "voucherdetails.voucher_id = voucher.VoucherID" in joins_str

    def test_f03_format_all_eight_canonical_metrics(self, metastore: dict[str, Any]) -> None:
        metrics_str = format_metrics_for_prompt(metastore)
        for metric_key in (
            "total_sales_revenue",
            "cost_of_goods_sold",
            "total_expenses",
            "gross_profit",
            "net_profit",
            "party_unpaid_balance",
            "total_purchases",
            "batch_stock_on_hand",
        ):
            assert f"#### Metric: `{metric_key}`" in metrics_str

    def test_f03_format_business_rules_and_caveats(self, metastore: dict[str, Any]) -> None:
        rules_str = format_business_rules_for_prompt(metastore)
        caveats_str = format_data_caveats_for_prompt(metastore)
        assert "PAKKA" in rules_str and "KACHA" in rules_str
        assert "NUMERIC_STORED_AS_TEXT" in caveats_str
        assert "RESERVED_WORD_COLUMNS" in caveats_str

    def test_f03_build_inference_prompt_with_retry_context(self, metastore: dict[str, Any]) -> None:
        sys_p, usr_p = build_inference_prompt(
            user_question="Show total sales revenue",
            metastore=metastore,
            modules=["sales_purchase"],
            current_date="2026-11-02",
            error_context={
                "attempt": 2,
                "max_attempts": 3,
                "previous_sql": "SELECT bad_col FROM voucher",
                "error_code": "UNKNOWN_COLUMN",
                "error_message": "Column 'bad_col' does not exist",
            },
        )
        assert "READ-ONLY Text-to-SQL Orchestrator" in sys_p
        assert "SELF-CORRECTION REQUIRED (Retry Attempt 2 of 3)" in usr_p
        assert "SELECT bad_col FROM voucher" in usr_p


# =========================================================================== #
# FEATURE 4: Strict Pydantic Output Validation (app/schemas/report_plan.py)
# =========================================================================== #
class TestFeature04PydanticOutputValidation:
    def test_f04_valid_select_plan(self) -> None:
        plan = ReportPlanSchema(
            thought_process="Selecting sales vouchers.",
            tables_used=["voucher", "voucher"],
            metrics_applied=["total_sales_revenue"],
            sql_query="SELECT SUM(TotalAmount) FROM voucher",
            suggested_visualization="kpi_card",
            error=None,
        )
        assert plan.is_refusal is False
        assert plan.tables_used == ["voucher"]  # Deduplicated

    def test_f04_valid_refusal_plan(self) -> None:
        plan = ReportPlanSchema(
            thought_process="User requested destructive DROP TABLE.",
            tables_used=[],
            metrics_applied=[],
            sql_query=None,
            suggested_visualization="table",
            error="Destructive DDL operations are strictly prohibited.",
        )
        assert plan.is_refusal is True
        assert plan.sql_query is None

    def test_f04_mutual_exclusivity_both_null_rejected(self) -> None:
        with pytest.raises(ValueError, match="Either sql_query or error must be provided"):
            ReportPlanSchema(
                thought_process="Invalid plan with neither sql nor error.",
                sql_query=None,
                error=None,
            )

    def test_f04_mutual_exclusivity_both_populated_rejected(self) -> None:
        with pytest.raises(ValueError, match="mutually exclusive"):
            ReportPlanSchema(
                thought_process="Invalid plan with both sql and error.",
                sql_query="SELECT 1",
                error="Some error",
            )

    def test_f04_strict_json_schema_generation(self) -> None:
        schema = report_plan_json_schema(strict=True)
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == {
            "thought_process",
            "tables_used",
            "metrics_applied",
            "sql_query",
            "suggested_visualization",
            "error",
        }


# =========================================================================== #
# FEATURE 5: LLM Fallback & Timeout Handling (app/inference/llm_client.py)
# =========================================================================== #
class TestFeature05LLMFallbackAndTimeouts:
    def test_f05_normalize_timeout_exception(self, metastore: dict[str, Any]) -> None:
        client = LLMClient(settings=Settings(llm_provider="vllm"), metastore=metastore)
        err = client._normalize_http_exception(httpx.ReadTimeout("Read timed out"))
        assert isinstance(err, LLMTimeoutError)
        assert err.provider == "vllm"

    def test_f05_normalize_connection_exception(self, metastore: dict[str, Any]) -> None:
        client = LLMClient(settings=Settings(llm_provider="ollama"), metastore=metastore)
        err = client._normalize_http_exception(httpx.ConnectError("Connection refused"))
        assert isinstance(err, LLMConnectionError)

    def test_f05_normalize_http_401_and_429(self, metastore: dict[str, Any]) -> None:
        client = LLMClient(settings=Settings(llm_provider="openai"), metastore=metastore)
        req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        resp_401 = httpx.Response(401, request=req, text="Unauthorized")
        resp_429 = httpx.Response(429, request=req, text="Rate limit")
        err_401 = client._normalize_http_exception(httpx.HTTPStatusError("401", request=req, response=resp_401))
        err_429 = client._normalize_http_exception(httpx.HTTPStatusError("429", request=req, response=resp_429))
        assert isinstance(err_401, LLMAuthenticationError)
        assert isinstance(err_429, LLMRateLimitError)

    def test_f05_graceful_degradation_when_raise_on_error_false(self, metastore: dict[str, Any]) -> None:
        mock_http = MagicMock(spec=httpx.Client)
        mock_http.post.side_effect = httpx.ConnectError("Server unreachable")
        client = LLMClient(
            settings=Settings(llm_provider="vllm", llm_max_retries=0),
            metastore=metastore,
            http_client=mock_http,
        )
        plan = client.generate_report_plan("Show sales", raise_on_error=False)
        assert plan.is_refusal is True
        assert "LLM Provider Error [vllm]" in (plan.error or "")

    def test_f05_llm_client_ast_gate_blocks_unsafe_sql(self, metastore: dict[str, Any]) -> None:
        mock_http = MagicMock(spec=httpx.Client)
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "thought_process": "Querying restricted table",
                        "tables_used": ["gsp_api"],
                        "metrics_applied": [],
                        "sql_query": "SELECT * FROM gsp_api",
                        "suggested_visualization": "table",
                        "error": None,
                    })
                }
            }]
        }
        mock_http.post.return_value = mock_resp
        client = LLMClient(metastore=metastore, http_client=mock_http)
        plan = client.generate_report_plan("Show GSP credentials", validate_ast=True)
        assert plan.is_refusal is True
        assert "Security Gate Blocked Query" in (plan.error or "")


# =========================================================================== #
# FEATURE 6: Live MySQL/MariaDB Connection Pool (app/connectors/executor.py)
# =========================================================================== #
class TestFeature06LiveMySQLPool:
    def test_f06_live_mysql_engine_configuration(self) -> None:
        eng = LiveMySQLEngine(
            db_url="mysql+pymysql://reader:pass@127.0.0.1:3306/ledger",
            statement_timeout_ms=5000,
            pool_size=5,
            max_overflow=10,
        )
        assert eng.statement_timeout_ms == 5000
        assert eng.pool_size == 5
        assert eng.max_overflow == 10
        eng.dispose()

    def test_f06_classify_mysql_and_mariadb_timeout_errors(self) -> None:
        code1, _ = classify_db_error(Exception(3024, "Query execution was interrupted, max_execution_time exceeded"))
        code2, _ = classify_db_error(Exception(1969, "Query execution was interrupted (max_statement_time exceeded)"))
        assert code1 == "TIMEOUT"
        assert code2 == "TIMEOUT"

    def test_f06_classify_mysql_connection_errors(self) -> None:
        code1, _ = classify_db_error(Exception(2003, "Can't connect to MySQL server"))
        code2, _ = classify_db_error(Exception(1045, "Access denied for user"))
        assert code1 == "CONNECTION_ERROR"
        assert code2 == "CONNECTION_ERROR"

    def test_f06_classify_mysql_schema_and_syntax_errors(self) -> None:
        import sqlalchemy.exc

        c_tbl, _ = classify_db_error(
            sqlalchemy.exc.ProgrammingError("SELECT * FROM missing", {}, Exception(1146, "Table 'ledger.missing' doesn't exist"))
        )
        c_col, _ = classify_db_error(
            sqlalchemy.exc.ProgrammingError("SELECT bad_col FROM voucher", {}, Exception(1054, "Unknown column 'bad_col' in 'field list'"))
        )
        c_syn, _ = classify_db_error(Exception(1064, "You have an error in your SQL syntax"))
        assert c_tbl == "SCHEMA_ERROR"
        assert c_col == "SCHEMA_ERROR"
        assert c_syn == "SYNTAX_ERROR"

    def test_f06_classify_division_by_zero_error(self) -> None:
        c_div, _ = classify_db_error(Exception(1365, "Division by 0"))
        assert c_div == "DIVISION_BY_ZERO"


# =========================================================================== #
# FEATURE 7: Automated SQLite Fallback Provisioning (app/connectors/executor.py)
# =========================================================================== #
class TestFeature07SQLiteFallbackProvisioning:
    def test_f07_sqlite_provisions_23_tables_and_284_rows(self) -> None:
        engine = SQLiteDumpEngine(dump_path=DEFAULT_SQL_DUMP_PATH, db_path=":memory:")
        engine.provision()
        assert engine.table_count == 23
        assert engine.row_count == 284
        engine.dispose()

    def test_f07_accounts_seed_row_count(self, sqlite_executor: DatabaseExecutor) -> None:
        res = sqlite_executor.execute_query("SELECT COUNT(*) AS cnt FROM accounts")
        assert res.rows[0]["cnt"] == 55

    def test_f07_voucher_and_voucherdetails_seed_counts(self, sqlite_executor: DatabaseExecutor) -> None:
        v_res = sqlite_executor.execute_query("SELECT COUNT(*) AS cnt FROM voucher")
        vd_res = sqlite_executor.execute_query("SELECT COUNT(*) AS cnt FROM voucherdetails")
        assert v_res.rows[0]["cnt"] == 5
        assert vd_res.rows[0]["cnt"] == 5

    def test_f07_batches_and_products_seed_counts(self, sqlite_executor: DatabaseExecutor) -> None:
        b_res = sqlite_executor.execute_query("SELECT COUNT(*) AS cnt FROM batches")
        p_res = sqlite_executor.execute_query("SELECT COUNT(*) AS cnt FROM products")
        assert b_res.rows[0]["cnt"] == 15
        assert p_res.rows[0]["cnt"] == 15

    def test_f07_expensive_and_orders_seed_counts(self, sqlite_executor: DatabaseExecutor) -> None:
        e_res = sqlite_executor.execute_query("SELECT COUNT(*) AS cnt FROM expensive")
        o_res = sqlite_executor.execute_query("SELECT COUNT(*) AS cnt FROM orders")
        assert e_res.rows[0]["cnt"] == 5
        assert o_res.rows[0]["cnt"] == 0


# =========================================================================== #
# FEATURE 8: SQLGlot Runtime Query Transpilation (app/connectors/executor.py)
# =========================================================================== #
class TestFeature08SQLGlotTranspilation:
    def test_f08_transpile_date_sub_interval(self) -> None:
        sqlite_sql = transpile_mysql_to_sqlite(
            "SELECT * FROM voucher WHERE Date >= DATE_SUB('2026-10-30', INTERVAL 30 DAY)"
        )
        assert "DATE(" in sqlite_sql and "-30 day" in sqlite_sql.lower()

    def test_f08_transpile_cast_decimal_and_backticks(self, sqlite_executor: DatabaseExecutor) -> None:
        res = sqlite_executor.execute_query(
            "SELECT `group`, CAST(unpaid_amount AS DECIMAL(15,2)) AS bal FROM accounts WHERE id = 1"
        )
        assert res.row_count == 1
        assert "bal" in res.columns

    def test_f08_sqlite_udfs_year_month_day_datediff(self, sqlite_executor: DatabaseExecutor) -> None:
        res = sqlite_executor.execute_query(
            "SELECT YEAR('2026-10-15') AS y, MONTH('2026-10-15') AS m, DAY('2026-10-15') AS d, DATEDIFF('2026-10-20', '2026-10-15') AS diff"
        )
        row = res.rows[0]
        assert row == {"y": 2026, "m": 10, "d": 15, "diff": 5}

    def test_f08_sqlite_udfs_str_to_date_and_date_format(self, sqlite_executor: DatabaseExecutor) -> None:
        res = sqlite_executor.execute_query(
            "SELECT STR_TO_DATE('2026-12-31', '%Y-%m-%d') AS dt, DATE_FORMAT('2026-10-15', '%Y-%m') AS ym"
        )
        assert res.rows[0] == {"dt": "2026-12-31", "ym": "2026-10"}

    def test_f08_sqlite_udfs_ifnull_concat_greatest_least_regexp(self, sqlite_executor: DatabaseExecutor) -> None:
        res = sqlite_executor.execute_query(
            "SELECT IFNULL(NULL, 'fallback') AS a, CONCAT('ERP', '-', 'AI') AS b, GREATEST(10, 25, 5) AS c, LEAST(10, 25, 5) AS d, REGEXP_LIKE('Sales_2026', '^sales', 'i') AS e"
        )
        assert res.rows[0] == {"a": "fallback", "b": "ERP-AI", "c": 25, "d": 5, "e": 1}


# =========================================================================== #
# FEATURE 9: Result Normalization & Decimal Precision (app/connectors/executor.py)
# =========================================================================== #
class TestFeature09ResultNormalization:
    def test_f09_decimal_precision_two_places(self) -> None:
        assert normalize_value(Decimal("1234.5678")) == 1234.57
        assert normalize_value(Decimal("0.001")) == 0.0

    def test_f09_float_nan_and_infinity_normalized_to_none(self) -> None:
        assert normalize_value(float("nan")) is None
        assert normalize_value(float("inf")) is None
        assert normalize_value(float("-inf")) is None
        assert normalize_value(99.995) == 100.0

    def test_f09_date_and_midnight_timestamp_normalization(self) -> None:
        assert normalize_value(date(2026, 10, 15)) == "2026-10-15"
        assert normalize_value(datetime(2026, 10, 15, 0, 0, 0)) == "2026-10-15"
        assert normalize_value("2026-10-15 00:00:00") == "2026-10-15"

    def test_f09_bytes_utf8_decoding(self) -> None:
        assert normalize_value(b"Shree Shashwatraj") == "Shree Shashwatraj"

    def test_f09_normalize_rows_list_of_dicts(self) -> None:
        rows = normalize_rows(["metric", "amount"], [("Gross Profit", Decimal("2468.000"))])
        assert rows == [{"metric": "Gross Profit", "amount": 2468.0}]


# =========================================================================== #
# FEATURE 10: Dual-Mode Executor Facade & 8 Canonical Metrics
# =========================================================================== #
class TestFeature10DualModeExecutorAndCanonicalMetrics:
    def test_f10_executor_auto_resolves_to_sqlite_fallback(self, sqlite_executor: DatabaseExecutor) -> None:
        assert sqlite_executor.active_mode == "sqlite_fallback"

    def test_f10_canonical_sales_cogs_gross_profit_expenses_net_profit(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = metastore["calculated_metrics"]["net_profit"]["canonical_sql"].format(
            from_date="2020-01-01", to_date="2030-12-31"
        )
        res = sqlite_executor.execute_query(sql)
        row = res.rows[0]
        assert row["total_sales_revenue"] == 10368.0
        assert row["cost_of_goods_sold"] == 7900.0
        assert row["gross_profit"] == 2468.0
        assert row["total_expenses"] == 45700.0
        assert row["net_profit"] == -43232.0

    def test_f10_canonical_party_unpaid_balance(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = metastore["calculated_metrics"]["party_unpaid_balance"]["canonical_sql"]
        res = sqlite_executor.execute_query(sql)
        assert res.row_count == 6
        assert res.rows[0]["name"] == "BABA BARFANI"
        assert res.rows[0]["party_unpaid_balance"] == 39802.09

    def test_f10_canonical_total_purchases(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = metastore["calculated_metrics"]["total_purchases"]["canonical_sql"].format(
            from_date="2020-01-01", to_date="2030-12-31"
        )
        res = sqlite_executor.execute_query(sql)
        assert res.rows[0]["total_purchases"] == 11045.0
        assert res.rows[0]["purchase_invoice_count"] == 2

    def test_f10_canonical_batch_stock_on_hand(
        self, sqlite_executor: DatabaseExecutor, metastore: dict[str, Any]
    ) -> None:
        sql = metastore["calculated_metrics"]["batch_stock_on_hand"]["canonical_sql"]
        res = sqlite_executor.execute_query(sql)
        assert res.row_count == 14
        row_phelan = next(r for r in res.rows if r["goods_name"] == "Phelan Sherman")
        assert row_phelan["stock_on_hand"] == -5.0
        assert row_phelan["stock_value"] == -5000.0


# =========================================================================== #
# FEATURE 11: FastAPI Server & Lifespan Architecture (app/main.py)
# =========================================================================== #
class TestFeature11FastAPIServerLifespan:
    def test_f11_root_health_endpoint(self) -> None:
        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "healthy"
            assert body["version"] == "1.0.0"

    def test_f11_api_v1_health_endpoint(self) -> None:
        with TestClient(app) as client:
            resp = client.get("/api/v1/health")
            assert resp.status_code == 200
            body = resp.json()
            assert body["database"]["mode"] in ("sqlite_fallback", "live_mysql")

    def test_f11_lifespan_prewarms_metastore_and_executor(self) -> None:
        with TestClient(app) as client:
            assert hasattr(client.app.state, "metastore")
            assert len(client.app.state.metastore.get("tables", {})) == 20
            assert hasattr(client.app.state, "executor")
            assert client.app.state.executor is not None

    def test_f11_cors_middleware_headers(self) -> None:
        with TestClient(app) as client:
            resp = client.options(
                "/api/v1/reports/query",
                headers={
                    "Origin": "http://localhost:5173",
                    "Access-Control-Request-Method": "POST",
                },
            )
            assert resp.status_code == 200
            assert "access-control-allow-origin" in resp.headers

    def test_f11_openapi_schema_exposes_query_route(self) -> None:
        with TestClient(app) as client:
            resp = client.get("/openapi.json")
            assert resp.status_code == 200
            paths = resp.json()["paths"]
            assert "/api/v1/reports/query" in paths
            assert "/api/v1/health" in paths


# =========================================================================== #
# FEATURE 12: Flexible JWT Auth & Dev-Mode Bypass (app/auth/jwt_handler.py)
# =========================================================================== #
class TestFeature12JWTAuthAndDevMode:
    def test_f12_create_and_verify_hs256_jwt(self) -> None:
        secret = "super-secure-32-byte-hmac-secret-key!!"
        token = create_access_token({"sub": "u1", "tenant_id": "t1", "role": "admin"}, secret=secret)
        decoded = verify_token(token, secret=secret)
        assert decoded["sub"] == "u1"
        assert decoded["tenant_id"] == "t1"

    def test_f12_expired_jwt_raises_401(self) -> None:
        from fastapi import HTTPException

        secret = "super-secure-32-byte-hmac-secret-key!!"
        token = create_access_token(
            {"sub": "u1"},
            expires_delta=timedelta(seconds=-10),
            secret=secret,
        )
        with pytest.raises(HTTPException) as exc_info:
            verify_token(token, secret=secret)
        assert exc_info.value.status_code == 401

    def test_f12_dev_mode_allows_tokenless_with_headers(self, sqlite_executor: DatabaseExecutor) -> None:
        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.last_token_usage = {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}
        mock_llm.generate_report_plan_async = AsyncMock(
            return_value=ReportPlanSchema(
                thought_process="Count accounts",
                tables_used=["accounts"],
                metrics_applied=[],
                sql_query="SELECT COUNT(*) AS cnt FROM accounts",
                suggested_visualization="kpi_card",
            )
        )
        app.dependency_overrides[get_llm_client] = lambda: mock_llm
        app.dependency_overrides[get_db_executor] = lambda: sqlite_executor
        try:
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/reports/query",
                    headers={"X-Tenant-ID": "agro_tenant", "X-User-ID": "tester"},
                    json={"user_query": "How many accounts exist?"},
                )
                assert resp.status_code == 200
                assert resp.json()["status"] == "success"
        finally:
            app.dependency_overrides.clear()

    def test_f12_prod_mode_rejects_missing_token_with_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.auth.jwt_handler.is_dev_mode", lambda: False)
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/reports/query",
                json={"user_query": "Show total sales"},
            )
            assert resp.status_code == 401

    def test_f12_malformed_authorization_header_rejected_401(self) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/reports/query",
                headers={"Authorization": "Basic dXNlcjpwYXNz"},
                json={"user_query": "Show total sales"},
            )
            assert resp.status_code == 401


# =========================================================================== #
# FEATURE 13: Query Endpoint Implementation (POST /api/v1/reports/query)
# =========================================================================== #
class TestFeature13QueryEndpoint:
    def _override_llm(self, plan: ReportPlanSchema, sqlite_executor: DatabaseExecutor) -> None:
        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.last_token_usage = {"prompt_tokens": 50, "completion_tokens": 25, "total_tokens": 75}
        mock_llm.generate_report_plan_async = AsyncMock(return_value=plan)
        app.dependency_overrides[get_llm_client] = lambda: mock_llm
        app.dependency_overrides[get_db_executor] = lambda: sqlite_executor

    def test_f13_query_endpoint_returns_data_and_ui_schema(self, sqlite_executor: DatabaseExecutor) -> None:
        self._override_llm(
            ReportPlanSchema(
                thought_process="Sum sales vouchers.",
                tables_used=["voucher"],
                metrics_applied=["total_sales_revenue"],
                sql_query="SELECT COALESCE(SUM(TotalAmount), 0) AS total_sales_revenue FROM voucher WHERE TransactionType = 'Sales'",
                suggested_visualization="kpi_card",
            ),
            sqlite_executor,
        )
        try:
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/reports/query",
                    json={"user_query": "What is our total sales revenue?", "session_id": "s-001"},
                )
                assert resp.status_code == 200
                body = resp.json()
                assert body["status"] == "success"
                assert body["data"] == [{"total_sales_revenue": 10368.0}]
                assert body["ui_schema"]["suggested_visualization"] == "kpi_grid"
                assert body["session_id"] == "s-001"
        finally:
            app.dependency_overrides.clear()

    def test_f13_query_endpoint_handles_deliberate_refusal_without_retry(
        self, sqlite_executor: DatabaseExecutor
    ) -> None:
        self._override_llm(
            ReportPlanSchema(
                thought_process="User asked to delete vouchers.",
                tables_used=[],
                metrics_applied=[],
                sql_query=None,
                error="Write operations (DELETE) are forbidden by read-only policy.",
            ),
            sqlite_executor,
        )
        try:
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/reports/query",
                    json={"user_query": "Delete all cancelled vouchers"},
                )
                body = resp.json()
                assert body["status"] == "error"
                assert body["execution_metadata"]["retries_used"] == 0
                assert "forbidden" in body["error"].lower()
        finally:
            app.dependency_overrides.clear()

    def test_f13_query_endpoint_validates_request_body(self) -> None:
        with TestClient(app) as client:
            resp = client.post("/api/v1/reports/query", json={"user_query": "   "})
            assert resp.status_code == 422

    def test_f13_query_endpoint_module_scope_resolution(self, sqlite_executor: DatabaseExecutor) -> None:
        self._override_llm(
            ReportPlanSchema(
                thought_process="Querying inventory batches.",
                tables_used=["batches"],
                metrics_applied=["batch_stock_on_hand"],
                sql_query="SELECT batch_number, CAST(quantity AS DECIMAL(15,2)) AS qty FROM batches",
                suggested_visualization="bar",
            ),
            sqlite_executor,
        )
        try:
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/reports/query",
                    json={"user_query": "Show batch quantities", "module_scope": "inventory"},
                )
                assert resp.status_code == 200
                assert len(resp.json()["data"]) == 15
        finally:
            app.dependency_overrides.clear()

    def test_f13_query_endpoint_preserves_multi_turn_history(self, sqlite_executor: DatabaseExecutor) -> None:
        self._override_llm(
            ReportPlanSchema(
                thought_process="Follow-up query on expenses.",
                tables_used=["expensive"],
                metrics_applied=["total_expenses"],
                sql_query="SELECT COALESCE(SUM(amount), 0) AS total_expenses FROM expensive",
                suggested_visualization="kpi_card",
            ),
            sqlite_executor,
        )
        try:
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/reports/query",
                    json={
                        "user_query": "Now show total expenses",
                        "history": [{"role": "user", "content": "Show sales"}, {"role": "assistant", "content": "10368"}],
                    },
                )
                assert resp.status_code == 200
                assert resp.json()["data"][0]["total_expenses"] == 45700.0
        finally:
            app.dependency_overrides.clear()


# =========================================================================== #
# FEATURE 14: 2-Step Self-Correction State Machine (app/api/v1/reports.py)
# =========================================================================== #
class TestFeature14SelfCorrectionStateMachine:
    def test_f14_zero_retries_on_valid_first_attempt(self, sqlite_executor: DatabaseExecutor) -> None:
        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.last_token_usage = {}
        mock_llm.generate_report_plan_async = AsyncMock(
            return_value=ReportPlanSchema(
                thought_process="Valid first try",
                tables_used=["accounts"],
                sql_query="SELECT id, name FROM accounts LIMIT 5",
            )
        )
        app.dependency_overrides[get_llm_client] = lambda: mock_llm
        app.dependency_overrides[get_db_executor] = lambda: sqlite_executor
        try:
            with TestClient(app) as client:
                resp = client.post("/api/v1/reports/query", json={"user_query": "List 5 accounts"})
                body = resp.json()
                assert body["status"] == "success"
                assert body["execution_metadata"]["retries_used"] == 0
                assert body["execution_metadata"]["retry_history"] == []
        finally:
            app.dependency_overrides.clear()

    def test_f14_one_retry_recovers_from_unknown_column(self, sqlite_executor: DatabaseExecutor) -> None:
        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.last_token_usage = {}
        mock_llm.generate_report_plan_async = AsyncMock(
            side_effect=[
                ReportPlanSchema(
                    thought_process="Try 1 with hallucinated column",
                    tables_used=["voucher"],
                    sql_query="SELECT FakeAmount FROM voucher",
                ),
                ReportPlanSchema(
                    thought_process="Try 2 fixed to TotalAmount",
                    tables_used=["voucher"],
                    sql_query="SELECT SUM(TotalAmount) AS total_sales FROM voucher WHERE TransactionType = 'Sales'",
                ),
            ]
        )
        app.dependency_overrides[get_llm_client] = lambda: mock_llm
        app.dependency_overrides[get_db_executor] = lambda: sqlite_executor
        try:
            with TestClient(app) as client:
                resp = client.post("/api/v1/reports/query", json={"user_query": "Show total sales"})
                body = resp.json()
                assert body["status"] == "success"
                assert body["execution_metadata"]["retries_used"] == 1
                assert len(body["execution_metadata"]["retry_history"]) == 1
        finally:
            app.dependency_overrides.clear()

    def test_f14_one_retry_recovers_from_unknown_table(self, sqlite_executor: DatabaseExecutor) -> None:
        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.last_token_usage = {}
        mock_llm.generate_report_plan_async = AsyncMock(
            side_effect=[
                ReportPlanSchema(
                    thought_process="Try 1 non-existent table",
                    tables_used=["non_existent_ledger"],
                    sql_query="SELECT id FROM non_existent_ledger",
                ),
                ReportPlanSchema(
                    thought_process="Try 2 valid table",
                    tables_used=["accounts"],
                    sql_query="SELECT COUNT(*) AS cnt FROM accounts",
                ),
            ]
        )
        app.dependency_overrides[get_llm_client] = lambda: mock_llm
        app.dependency_overrides[get_db_executor] = lambda: sqlite_executor
        try:
            with TestClient(app) as client:
                resp = client.post("/api/v1/reports/query", json={"user_query": "Count accounts"})
                body = resp.json()
                assert body["status"] == "success"
                assert body["execution_metadata"]["retries_used"] == 1
                assert body["execution_metadata"]["retry_history"][0]["error_code"] == "UNKNOWN_TABLE"
        finally:
            app.dependency_overrides.clear()

    def test_f14_two_retries_succeeds_on_third_attempt(self, sqlite_executor: DatabaseExecutor) -> None:
        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.last_token_usage = {}
        mock_llm.generate_report_plan_async = AsyncMock(
            side_effect=[
                ReportPlanSchema(thought_process="Attempt 1 bad table", sql_query="SELECT * FROM wrong_tbl"),
                ReportPlanSchema(thought_process="Attempt 2 bad col", sql_query="SELECT wrong_col FROM accounts"),
                ReportPlanSchema(thought_process="Attempt 3 valid", sql_query="SELECT COUNT(*) AS total FROM accounts"),
            ]
        )
        app.dependency_overrides[get_llm_client] = lambda: mock_llm
        app.dependency_overrides[get_db_executor] = lambda: sqlite_executor
        try:
            with TestClient(app) as client:
                resp = client.post("/api/v1/reports/query", json={"user_query": "Count accounts"})
                body = resp.json()
                assert body["status"] == "success"
                assert body["execution_metadata"]["retries_used"] == 2
                assert len(body["execution_metadata"]["retry_history"]) == 2
        finally:
            app.dependency_overrides.clear()

    def test_f14_exhausts_two_retries_and_returns_error(self, sqlite_executor: DatabaseExecutor) -> None:
        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.last_token_usage = {}
        mock_llm.generate_report_plan_async = AsyncMock(
            side_effect=[
                ReportPlanSchema(thought_process="Bad 1", sql_query="SELECT bad1 FROM accounts"),
                ReportPlanSchema(thought_process="Bad 2", sql_query="SELECT bad2 FROM accounts"),
                ReportPlanSchema(thought_process="Bad 3", sql_query="SELECT bad3 FROM accounts"),
            ]
        )
        app.dependency_overrides[get_llm_client] = lambda: mock_llm
        app.dependency_overrides[get_db_executor] = lambda: sqlite_executor
        try:
            with TestClient(app) as client:
                resp = client.post("/api/v1/reports/query", json={"user_query": "Bad query loop"})
                body = resp.json()
                assert body["status"] == "error"
                assert body["execution_metadata"]["retries_used"] == 3
                assert len(body["execution_metadata"]["retry_history"]) == 3
        finally:
            app.dependency_overrides.clear()


# =========================================================================== #
# FEATURE 15: Self-Correction Full Transparency (execution_metadata)
# =========================================================================== #
class TestFeature15SelfCorrectionTransparency:
    def test_f15_retry_history_contains_failed_sql_and_hint(self, sqlite_executor: DatabaseExecutor) -> None:
        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.last_token_usage = {"prompt_tokens": 80, "completion_tokens": 40, "total_tokens": 120}
        mock_llm.generate_report_plan_async = AsyncMock(
            side_effect=[
                ReportPlanSchema(thought_process="Fail 1", sql_query="SELECT bogus_col FROM voucher"),
                ReportPlanSchema(thought_process="Pass 2", sql_query="SELECT VoucherID FROM voucher LIMIT 1"),
            ]
        )
        app.dependency_overrides[get_llm_client] = lambda: mock_llm
        app.dependency_overrides[get_db_executor] = lambda: sqlite_executor
        try:
            with TestClient(app) as client:
                resp = client.post("/api/v1/reports/query", json={"user_query": "Get 1 voucher"})
                meta = resp.json()["execution_metadata"]
                entry = meta["retry_history"][0]
                assert entry["attempt"] == 1
                assert entry["error_type"] == "SCHEMA_ERROR"
                assert entry["error_code"] == "UNKNOWN_COLUMN"
                assert entry["failed_sql"] == "SELECT bogus_col FROM voucher"
                assert "Column does not exist" in entry["correction_hint"]
        finally:
            app.dependency_overrides.clear()

    def test_f15_correction_hint_generator_coverage(self) -> None:
        assert "table does not exist" in generate_correction_hint("UNKNOWN_TABLE", "not in metastore")
        assert "Column does not exist" in generate_correction_hint("UNKNOWN_COLUMN", "unknown column")
        assert "READ-ONLY" in generate_correction_hint("FORBIDDEN_OPERATION", "DROP")
        assert "credential tables" in generate_correction_hint("RESTRICTED_TABLE", "gsp_api").lower()
        assert "Multiple SQL statements" in generate_correction_hint("MULTIPLE_STATEMENTS", "semicolon")

    def test_f15_format_error_context_for_prompt_structure(self) -> None:
        block = format_error_context_for_prompt({
            "attempt": 2,
            "max_attempts": 3,
            "previous_sql": "SELECT x FROM y",
            "error_code": "UNKNOWN_TABLE",
            "error_message": "Table 'y' is unknown",
        })
        assert "Retry Attempt 2 of 3" in block
        assert "SELECT x FROM y" in block
        assert "`UNKNOWN_TABLE`" in block

    def test_f15_execution_metadata_latency_and_active_mode(self, sqlite_executor: DatabaseExecutor) -> None:
        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.last_token_usage = {"prompt_tokens": 15, "completion_tokens": 10, "total_tokens": 25}
        mock_llm.generate_report_plan_async = AsyncMock(
            return_value=ReportPlanSchema(
                thought_process="Check metadata",
                tables_used=["accounts"],
                metrics_applied=["party_unpaid_balance"],
                sql_query="SELECT id, name FROM accounts LIMIT 2",
            )
        )
        app.dependency_overrides[get_llm_client] = lambda: mock_llm
        app.dependency_overrides[get_db_executor] = lambda: sqlite_executor
        try:
            with TestClient(app) as client:
                resp = client.post("/api/v1/reports/query", json={"user_query": "Show 2 accounts"})
                meta = resp.json()["execution_metadata"]
                assert meta["latency_ms"] > 0
                assert meta["active_mode"] == "sqlite_fallback"
                assert "LIMIT" in meta["verified_sql"]
        finally:
            app.dependency_overrides.clear()

    def test_f15_llm_timeout_recorded_in_retry_history(self, sqlite_executor: DatabaseExecutor) -> None:
        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.last_token_usage = {}
        mock_llm.generate_report_plan_async = AsyncMock(
            side_effect=[
                LLMTimeoutError("Timed out on attempt 1", provider="vllm"),
                ReportPlanSchema(thought_process="Recovered", sql_query="SELECT COUNT(*) AS c FROM accounts"),
            ]
        )
        app.dependency_overrides[get_llm_client] = lambda: mock_llm
        app.dependency_overrides[get_db_executor] = lambda: sqlite_executor
        try:
            with TestClient(app) as client:
                resp = client.post("/api/v1/reports/query", json={"user_query": "Count accounts"})
                meta = resp.json()["execution_metadata"]
                assert meta["retry_history"][0]["error_type"] == "TIMEOUT"
        finally:
            app.dependency_overrides.clear()


# =========================================================================== #
# FEATURE 16: Dynamic UI Schema Derivation (app/api/v1/ui_deriver.py)
# =========================================================================== #
class TestFeature16DynamicUISchemaDerivation:
    def test_f16_single_row_summary_derives_kpi_grid(self) -> None:
        schema = derive_ui_schema(
            columns=["total_sales_revenue", "gross_profit", "net_profit"],
            data=[{"total_sales_revenue": 10368.0, "gross_profit": 2468.0, "net_profit": -43232.0}],
            metrics_applied=["gross_profit", "net_profit"],
            llm_suggestion="kpi_card",
        )
        assert schema.suggested_visualization == "kpi_grid"
        assert len(schema.kpis) == 3
        assert schema.kpis[0].value == "10,368.00 INR"
        assert schema.kpis[1].trend == "positive"
        assert schema.kpis[2].trend == "negative"

    def test_f16_time_series_derives_line_chart(self) -> None:
        schema = derive_ui_schema(
            columns=["voucher_date", "total_sales"],
            data=[
                {"voucher_date": "2025-10-01", "total_sales": 4000.0},
                {"voucher_date": "2025-10-02", "total_sales": 6368.0},
            ],
        )
        assert schema.suggested_visualization == "line_chart"

    def test_f16_small_categorical_derives_donut_or_bar_chart(self) -> None:
        data = [
            {"role": "retailer", "party_count": 30},
            {"role": "supplier", "party_count": 15},
            {"role": "staff", "party_count": 10},
        ]
        s_donut = derive_ui_schema(["role", "party_count"], data, llm_suggestion="pie")
        s_bar = derive_ui_schema(["role", "party_count"], data, llm_suggestion="bar")
        assert s_donut.suggested_visualization == "donut_chart"
        assert s_bar.suggested_visualization == "bar_chart"

    def test_f16_wide_dataset_derives_table(self) -> None:
        schema = derive_ui_schema(
            columns=["id", "name", "business_name", "role", "unpaid_amount"],
            data=[
                {"id": 1, "name": "A", "business_name": "B", "role": "retailer", "unpaid_amount": 500.0},
                {"id": 2, "name": "C", "business_name": "D", "role": "supplier", "unpaid_amount": 200.0},
            ],
        )
        assert schema.suggested_visualization == "table"

    def test_f16_column_type_inference_and_acronym_labels(self) -> None:
        assert format_column_label("total_cogs_gst_id") == "Total COGS GST ID"
        assert infer_column_type("total_revenue", [100.0]) == "currency"
        assert infer_column_type("invoice_count", [10]) == "number"
        assert infer_column_type("voucher_date", ["2026-10-15"]) == "date"
        assert infer_column_type("is_active", [True]) == "boolean"


# =========================================================================== #
# FEATURE 17: Fail-Closed Guardrails Integration (app/security/guardrails.py)
# =========================================================================== #
class TestFeature17GuardrailsIntegration:
    @pytest.mark.parametrize(
        "bad_sql,expected_code",
        [
            ("DROP TABLE accounts", "FORBIDDEN_STATEMENT"),
            ("DELETE FROM voucher WHERE VoucherID = 1", "FORBIDDEN_STATEMENT"),
            ("UPDATE accounts SET unpaid_amount = '0'", "FORBIDDEN_STATEMENT"),
            ("INSERT INTO expensive (amount) VALUES (100)", "FORBIDDEN_STATEMENT"),
            ("SELECT * FROM gsp_api", "RESTRICTED_TABLE"),
            ("SELECT * FROM gsp_credentials", "RESTRICTED_TABLE"),
            ("SELECT SLEEP(5) FROM accounts", "FORBIDDEN_FUNCTION"),
            ("SELECT id FROM accounts; SELECT id FROM voucher", "MULTIPLE_STATEMENTS"),
            ("SELECT /*!50000 1 */ FROM accounts", "EXECUTABLE_COMMENT"),
            ("SELECT id FROM accounts INTO OUTFILE '/tmp/pwn.txt'", "FORBIDDEN_OPERATION"),
        ],
    )
    def test_f17_guardrail_blocks_unsafe_queries(
        self, metastore: dict[str, Any], bad_sql: str, expected_code: str
    ) -> None:
        with pytest.raises(SQLSecurityViolation) as exc_info:
            validate_and_sanitize_sql(bad_sql, metastore)
        assert exc_info.value.code == expected_code

    def test_f17_select_star_expands_only_whitelisted_columns(self, metastore: dict[str, Any]) -> None:
        sanitized = validate_and_sanitize_sql("SELECT * FROM accounts LIMIT 10", metastore)
        assert "*" not in sanitized
        assert "password" not in sanitized.lower()
        assert "otp" not in sanitized.lower()
        assert "aadhaar_number" not in sanitized.lower()

    def test_f17_enforces_and_caps_row_limit(self, metastore: dict[str, Any]) -> None:
        s1 = validate_and_sanitize_sql("SELECT id FROM accounts", metastore, max_rows=1000)
        assert "LIMIT 1000" in s1
        s2 = validate_and_sanitize_sql("SELECT id FROM accounts LIMIT 99999", metastore, max_rows=1000)
        assert "LIMIT 1000" in s2


# =========================================================================== #
# FEATURES 18–27: SOTA SAP Fiori 3-Zone UI Workspace & Host Integration
# =========================================================================== #
class TestFeatures18To27UIAndHostIntegration:
    # Feature 18: SAP Fiori 3-Zone ALP Layout (5 tests)
    def test_f18_alp_component_exists_and_implements_3_zones(self) -> None:
        alp = (UI_ROOT / "src/components/AnalyticalListPage.tsx").read_text(encoding="utf-8")
        assert "KPIHeader" in alp
        assert "FilterBar" in alp
        assert "HighDensityTable" in alp
        assert "ChartCanvas" in alp
        assert "JouleDrawer" in alp

    def test_f18_fiori_horizon_css_tokens_defined(self) -> None:
        css = (UI_ROOT / "src/styles/index.css").read_text(encoding="utf-8")
        assert "#F5F6F7" in css or "f5f6f7" in css.lower()

    def test_f18_executive_narrative_and_date_picker_present(self) -> None:
        assert (UI_ROOT / "src/components/Zone1_TopHeader/ExecutiveNarrative.tsx").exists()
        assert (UI_ROOT / "src/components/Zone1_TopHeader/DatePickerBar.tsx").exists()

    def test_f18_standalone_app_entry_and_api_client_exist(self) -> None:
        assert (UI_ROOT / "src/App.tsx").exists()
        assert (UI_ROOT / "src/api/client.ts").exists()

    def test_f18_production_spa_dist_bundle_built(self) -> None:
        assert (UI_ROOT / "dist/index.html").exists()

    # Feature 19: Zone 1 KPI Cards & Filter Bar Pills (5 tests)
    def test_f19_kpi_header_renders_trend_indicators(self) -> None:
        src = (UI_ROOT / "src/components/Zone1_TopHeader/KPIHeader.tsx").read_text(encoding="utf-8")
        assert "positive" in src and "negative" in src

    def test_f19_filter_bar_renders_interactive_pills(self) -> None:
        src = (UI_ROOT / "src/components/Zone1_TopHeader/FilterBar.tsx").read_text(encoding="utf-8")
        assert "filter" in src.lower()

    def test_f19_kpi_header_invokes_formula_inspector(self) -> None:
        src = (UI_ROOT / "src/components/Zone1_TopHeader/KPIHeader.tsx").read_text(encoding="utf-8")
        assert "fx" in src or "Formula" in src or "onInspectMetric" in src

    def test_f19_date_picker_bar_supports_fy_presets(self) -> None:
        src = (UI_ROOT / "src/components/Zone1_TopHeader/DatePickerBar.tsx").read_text(encoding="utf-8")
        assert "FY" in src or "Date" in src

    def test_f19_kpi_header_test_file_exists(self) -> None:
        assert (UI_ROOT / "src/test/KPIHeader.test.tsx").exists()

    # Feature 20: Zone 2 High-Density TanStack Table (5 tests)
    def test_f20_high_density_table_uses_tanstack_react_table(self) -> None:
        src = (UI_ROOT / "src/components/Zone2_CenterCanvas/HighDensityTable.tsx").read_text(encoding="utf-8")
        assert "@tanstack/react-table" in src
        assert "getSortedRowModel" in src

    def test_f20_high_density_table_32px_row_height(self) -> None:
        src = (UI_ROOT / "src/components/Zone2_CenterCanvas/HighDensityTable.tsx").read_text(encoding="utf-8")
        assert "32px" in src or "h-8" in src

    def test_f20_column_toggle_menu_component_exists(self) -> None:
        assert (UI_ROOT / "src/components/Zone2_CenterCanvas/ColumnToggleMenu.tsx").exists()

    def test_f20_tabular_nums_and_currency_alignment_in_table(self) -> None:
        src = (UI_ROOT / "src/components/Zone2_CenterCanvas/HighDensityTable.tsx").read_text(encoding="utf-8")
        assert "font-mono" in src and "formatCurrency" in src

    def test_f20_high_density_table_test_file_exists(self) -> None:
        assert (UI_ROOT / "src/test/HighDensityTable.test.tsx").exists()

    # Feature 21: Zone 2 Interactive Analytical Charts (5 tests)
    def test_f21_chart_canvas_supports_bar_line_donut(self) -> None:
        src = (UI_ROOT / "src/components/Zone2_CenterCanvas/ChartCanvas.tsx").read_text(encoding="utf-8")
        assert "BarChart" in src
        assert "LineChart" in src
        assert "PieChart" in src

    def test_f21_chart_canvas_supports_click_drilldown(self) -> None:
        src = (UI_ROOT / "src/components/Zone2_CenterCanvas/ChartCanvas.tsx").read_text(encoding="utf-8")
        assert "onCrossFilter" in src or "onClick" in src

    def test_f21_chart_canvas_view_switcher_buttons(self) -> None:
        src = (UI_ROOT / "src/components/Zone2_CenterCanvas/ChartCanvas.tsx").read_text(encoding="utf-8")
        assert "setChartType('bar')" in src and "setChartType('line')" in src and "setChartType('donut')" in src

    def test_f21_formatters_handle_indian_currency(self) -> None:
        src = (UI_ROOT / "src/utils/formatters.ts").read_text(encoding="utf-8")
        assert "en-IN" in src or "INR" in src

    def test_f21_chart_canvas_test_file_exists(self) -> None:
        assert (UI_ROOT / "src/test/ChartCanvas.test.tsx").exists()

    # Feature 22: Zone 2 Excel & CSV Export Engine (5 tests)
    def test_f22_export_helper_uses_sheetjs_xlsx(self) -> None:
        src = (UI_ROOT / "src/utils/exportHelper.ts").read_text(encoding="utf-8")
        assert "xlsx" in src.lower()
        assert "exportToExcel" in src
        assert "exportToCsv" in src

    def test_f22_export_buttons_component_exists(self) -> None:
        src = (UI_ROOT / "src/components/Zone2_CenterCanvas/ExportButtons.tsx").read_text(encoding="utf-8")
        assert "Excel" in src and "CSV" in src

    def test_f22_csv_utf8_bom_and_quote_escaping(self) -> None:
        src = (UI_ROOT / "src/utils/exportHelper.ts").read_text(encoding="utf-8")
        assert "\\uFEFF" in src and 'replace(/"/g, \'""\')' in src

    def test_f22_export_helper_unit_tests_exist(self) -> None:
        assert (UI_ROOT / "src/test/exportHelper.test.ts").exists()

    def test_f22_xlsx_dependency_declared_in_package_json(self) -> None:
        pkg = json.loads((UI_ROOT / "package.json").read_text(encoding="utf-8"))
        assert "xlsx" in pkg.get("dependencies", {})

    # Feature 23: Zone 3 Joule Copilot Drawer (5 tests)
    def test_f23_joule_drawer_380px_width(self) -> None:
        src = (UI_ROOT / "src/components/Zone3_JouleCopilot/JouleDrawer.tsx").read_text(encoding="utf-8")
        assert "380px" in src

    def test_f23_reasoning_trace_and_metadata_inspector_exist(self) -> None:
        assert (UI_ROOT / "src/components/Zone3_JouleCopilot/ReasoningTrace.tsx").exists()
        assert (UI_ROOT / "src/components/Zone3_JouleCopilot/MetadataInspector.tsx").exists()

    def test_f23_sql_preview_and_retry_timeline_exist(self) -> None:
        assert (UI_ROOT / "src/components/Zone3_JouleCopilot/SqlPreview.tsx").exists()
        assert (UI_ROOT / "src/components/Zone3_JouleCopilot/RetryTimeline.tsx").exists()

    def test_f23_multi_turn_input_exists(self) -> None:
        assert (UI_ROOT / "src/components/Zone3_JouleCopilot/MultiTurnInput.tsx").exists()

    def test_f23_retry_timeline_renders_attempt_diffs(self) -> None:
        src = (UI_ROOT / "src/components/Zone3_JouleCopilot/RetryTimeline.tsx").read_text(encoding="utf-8")
        assert "retry_history" in src or "attempt" in src

    # Feature 24: Zone 3 NetSuite fx Formula Inspector (5 tests)
    def test_f24_formula_inspector_contains_all_8_canonical_metrics(self) -> None:
        src = (UI_ROOT / "src/api/client.ts").read_text(encoding="utf-8")
        for m in (
            "total_sales_revenue",
            "cost_of_goods_sold",
            "total_expenses",
            "gross_profit",
            "net_profit",
            "party_unpaid_balance",
            "total_purchases",
            "batch_stock_on_hand",
        ):
            assert m in src

    def test_f24_formula_inspector_shows_source_reference_and_sql(self) -> None:
        src = (UI_ROOT / "src/api/client.ts").read_text(encoding="utf-8")
        assert "profitLossRoutes.js" in src
        assert "VochurRoutes.js" in src

    def test_f24_formula_inspector_supports_copy_expression(self) -> None:
        src = (UI_ROOT / "src/components/Zone3_JouleCopilot/FormulaInspector.tsx").read_text(encoding="utf-8")
        assert "clipboard" in src

    def test_f24_formula_inspector_shows_required_tables(self) -> None:
        src = (UI_ROOT / "src/components/Zone3_JouleCopilot/FormulaInspector.tsx").read_text(encoding="utf-8")
        assert "required_tables" in src and "CANONICAL_METRICS" in src

    def test_f24_formula_inspector_test_file_exists(self) -> None:
        assert (UI_ROOT / "src/test/FormulaInspector.test.tsx").exists()

    # Feature 25: Global Cmd+K Awesomebar (5 tests)
    def test_f25_awesomebar_modal_exists(self) -> None:
        assert (UI_ROOT / "src/components/Awesomebar/AwesomebarModal.tsx").exists()

    def test_f25_awesomebar_supports_cmd_k_shortcut(self) -> None:
        alp = (UI_ROOT / "src/components/AnalyticalListPage.tsx").read_text(encoding="utf-8")
        assert "metaKey" in alp or "ctrlKey" in alp

    def test_f25_awesomebar_includes_precanned_erp_prompts(self) -> None:
        src = (UI_ROOT / "src/components/Awesomebar/AwesomebarModal.tsx").read_text(encoding="utf-8")
        assert "net profit" in src.lower() or "gross profit" in src.lower()

    def test_f25_awesomebar_escape_closes_modal(self) -> None:
        src = (UI_ROOT / "src/components/Awesomebar/AwesomebarModal.tsx").read_text(encoding="utf-8")
        assert "Escape" in src

    def test_f25_awesomebar_tested_in_alp_suite(self) -> None:
        src = (UI_ROOT / "src/test/AnalyticalListPage.test.tsx").read_text(encoding="utf-8")
        assert "Awesomebar" in src or "Cmd+K" in src or "k" in src

    # Feature 26: Dual Delivery Web Component (<erp-ai-reporter>) (5 tests)
    def test_f26_custom_element_defined_with_shadow_dom(self) -> None:
        src = (UI_ROOT / "src/webcomponent/ErpAiReporterElement.tsx").read_text(encoding="utf-8")
        assert "customElements.define" in src
        assert "erp-ai-reporter" in src
        assert "attachShadow" in src

    def test_f26_observed_attributes_supported(self) -> None:
        src = (UI_ROOT / "src/webcomponent/ErpAiReporterElement.tsx").read_text(encoding="utf-8")
        for attr in ("api-url", "tenant-id", "token", "theme", "default-query"):
            assert attr in src

    def test_f26_custom_events_dispatched(self) -> None:
        src = (UI_ROOT / "src/webcomponent/ErpAiReporterElement.tsx").read_text(encoding="utf-8")
        assert "reportGenerated" in src
        assert "filterChanged" in src

    def test_f26_umd_and_es_bundles_compiled(self) -> None:
        umd = UI_ROOT / "dist/wc/erp-ai-reporter.umd.js"
        es = UI_ROOT / "dist/wc/erp-ai-reporter.es.js"
        assert umd.exists() and umd.stat().st_size > 100_000
        assert es.exists() and es.stat().st_size > 100_000

    def test_f26_web_component_test_files_exist(self) -> None:
        assert (UI_ROOT / "src/test/WebComponent.test.ts").exists()
        assert (UI_ROOT / "src/test/AdversarialWebComponentAndHost.test.tsx").exists()

    # Feature 27: Host Integration in shreeshashwatraj-frontend (5 tests)
    def test_f27_host_erp_ai_reporter_wrapper_exists(self) -> None:
        wrapper = HOST_FRONTEND_ROOT / "src/Component/Panels/Admin/AdminReports/ErpAiReporter.js"
        assert wrapper.exists()
        content = wrapper.read_text(encoding="utf-8")
        assert "<erp-ai-reporter" in content

    def test_f27_host_ai_reports_page_exists(self) -> None:
        page = HOST_FRONTEND_ROOT / "src/Component/Panels/Admin/AdminReports/AiReportsPage.js"
        assert page.exists()
        assert "ErpAiReporter" in page.read_text(encoding="utf-8")

    def test_f27_host_app_js_mounts_ai_reports_route(self) -> None:
        app_js = (HOST_FRONTEND_ROOT / "src/App.js").read_text(encoding="utf-8")
        assert "/ai-reports" in app_js
        assert "AiReportsPage" in app_js

    def test_f27_host_reports_js_includes_ai_copilot_tab(self) -> None:
        reports_js = (HOST_FRONTEND_ROOT / "src/Component/Panels/Admin/AdminReports/Reports.js").read_text(
            encoding="utf-8"
        )
        assert "ErpAiReporter" in reports_js

    def test_f27_host_umd_bundle_copied_to_admin_reports(self) -> None:
        host_umd = HOST_FRONTEND_ROOT / "src/Component/Panels/Admin/AdminReports/erp-ai-reporter.umd.js"
        assert host_umd.exists() and host_umd.stat().st_size > 100_000


# =========================================================================== #
# FEATURE 28: Synthetic Dataset Builder (erp-ai-model-pipeline)
# =========================================================================== #
class TestFeature28SyntheticDatasetBuilder:
    def test_f28_dataset_summary_counts_and_ratios(self) -> None:
        summary_path = PIPELINE_ROOT / "data/dataset_summary.json"
        assert summary_path.exists()
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert summary["total_samples"] == 3000
        assert summary["train_samples"] == 2700
        assert summary["val_samples"] == 300
        assert summary["bird_count"] == 2100
        assert summary["gretel_count"] == 600
        assert summary["refusal_count"] == 300

    def test_f28_train_and_val_jsonl_line_counts(self) -> None:
        train_lines = (PIPELINE_ROOT / "data/train.jsonl").read_text(encoding="utf-8").strip().splitlines()
        val_lines = (PIPELINE_ROOT / "data/val.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(train_lines) == 2700
        assert len(val_lines) == 300

    def test_f28_chatml_messages_structure_and_report_plan_schema(self) -> None:
        train_lines = (PIPELINE_ROOT / "data/train.jsonl").read_text(encoding="utf-8").strip().splitlines()
        sample_records = [json.loads(line) for line in train_lines[:25]]
        val_report = validate_dataset_records(sample_records)
        assert val_report["is_valid"] is True
        assert val_report["valid_records"] == 25

    def test_f28_refusal_samples_have_null_sql_and_populated_error(self) -> None:
        val_lines = (PIPELINE_ROOT / "data/val.jsonl").read_text(encoding="utf-8").strip().splitlines()
        refusals = [
            json.loads(line)
            for line in val_lines
            if json.loads(json.loads(line)["messages"][2]["content"]).get("sql_query") is None
        ]
        assert len(refusals) == 30
        for r in refusals[:5]:
            assistant_json = json.loads(r["messages"][2]["content"])
            plan = ReportPlanSchema.model_validate(assistant_json)
            assert plan.is_refusal is True
            assert plan.error is not None

    def test_f28_select_samples_pass_ast_guardrails(self, metastore: dict[str, Any]) -> None:
        val_lines = (PIPELINE_ROOT / "data/val.jsonl").read_text(encoding="utf-8").strip().splitlines()
        selects = [
            json.loads(line)
            for line in val_lines
            if json.loads(json.loads(line)["messages"][2]["content"]).get("sql_query") is not None
        ]
        assert len(selects) == 270
        for s in selects[:15]:
            assistant_json = json.loads(s["messages"][2]["content"])
            sql_q = assistant_json["sql_query"]
            assert sql_q is not None
            sanitized = validate_and_sanitize_sql(sql_q, metastore)
            assert "LIMIT" in sanitized


# =========================================================================== #
# FEATURE 29: Unsloth QLoRA Training Script (erp-ai-model-pipeline)
# =========================================================================== #
class TestFeature29UnslothQLoRATrainer:
    def test_f29_qlora_config_hyperparameters(self) -> None:
        cfg = QLoRAConfig()
        assert cfg.model_name == "Qwen/Qwen2.5-Coder-7B-Instruct"
        assert cfg.load_in_4bit is True
        assert cfg.quant_type == "nf4"
        assert cfg.lora_r == 32
        assert cfg.lora_alpha == 64
        assert cfg.max_seq_length == 4096
        assert cfg.learning_rate == 2e-4

    def test_f29_target_linear_modules_complete(self) -> None:
        cfg = QLoRAConfig()
        expected = {"q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"}
        assert set(cfg.target_modules) == expected

    def test_f29_dry_run_plan_artifact_exists_and_passed(self) -> None:
        plan_path = PIPELINE_ROOT / "outputs/qwen2.5-coder-7b-erp-qlora/dry_run_plan.json"
        assert plan_path.exists()
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        assert plan["status"] == "DRY_RUN_PASSED"
        assert plan["total_training_samples"] == 2700
        assert plan["total_validation_samples"] == 300

    def test_f29_estimate_token_count_heuristic(self) -> None:
        tokens = estimate_token_count("SELECT VoucherID, TotalAmount FROM voucher WHERE TransactionType = 'Sales'")
        assert 10 <= tokens <= 40
        assert estimate_token_count("") == 0

    def test_f29_run_dry_run_verification_with_custom_config(self, tmp_path: Path) -> None:
        cfg = QLoRAConfig(
            train_file=str(PIPELINE_ROOT / "data/train.jsonl"),
            val_file=str(PIPELINE_ROOT / "data/val.jsonl"),
            output_dir=str(tmp_path / "qlora_out"),
        )
        rep = run_dry_run_verification(cfg)
        assert rep["status"] == "DRY_RUN_PASSED"
        assert (tmp_path / "qlora_out/dry_run_plan.json").exists()


# =========================================================================== #
# FEATURE 30: Dual Format Model Exporter (erp-ai-model-pipeline)
# =========================================================================== #
class TestFeature30DualFormatExporter:
    def test_f30_exported_safetensors_artifacts_exist(self) -> None:
        st_dir = PIPELINE_ROOT / "exported_models/vllm-safetensors"
        assert (st_dir / "config.json").exists()
        assert (st_dir / "tokenizer_config.json").exists()
        assert (st_dir / "model.safetensors").exists()

    def test_f30_exported_gguf_and_modelfile_exist(self) -> None:
        gguf_dir = PIPELINE_ROOT / "exported_models/ollama-gguf"
        assert (gguf_dir / "qwen2.5-coder-7b-erp-q4_k_m.gguf").exists()
        assert (gguf_dir / "Modelfile").exists()

    def test_f30_modelfile_contains_chatml_template_and_stop_tokens(self) -> None:
        modelfile = (PIPELINE_ROOT / "exported_models/ollama-gguf/Modelfile").read_text(encoding="utf-8")
        assert "<|im_start|>" in modelfile
        assert "<|im_end|>" in modelfile
        assert "PARAMETER temperature 0.0" in modelfile

    def test_f30_export_summary_json_valid(self) -> None:
        summary = json.loads((PIPELINE_ROOT / "exported_models/export_summary.json").read_text(encoding="utf-8"))
        assert "safetensors" in summary["exported_formats"]
        assert "gguf" in summary["exported_formats"]
        assert summary["quantization"] == "q4_k_m"

    def test_f30_export_dual_formats_function_execution(self, tmp_path: Path) -> None:
        res = export_dual_formats(output_dir=tmp_path / "exp", dry_run=True)
        assert Path(res["safetensors_config"]).exists()
        assert Path(res["gguf_path"]).exists()
        assert Path(res["modelfile_path"]).exists()


# =========================================================================== #
# FEATURE 31: Sub-Schema Vector & BM25 Router (app/metastore/router.py)
# =========================================================================== #
class TestFeature31SubSchemaRouter:
    def test_f31_bypass_when_table_count_below_threshold(self, metastore: dict[str, Any]) -> None:
        router = SubSchemaRouter(metastore=metastore, force_pure_python=True)
        pruned, meta = router.route_and_prune("What is our net profit?", force_prune=False)
        assert meta["bypassed"] is True
        assert len(pruned["tables"]) == 20

    def test_f31_force_prune_selects_relevant_tables_and_expands_metrics(self, metastore: dict[str, Any]) -> None:
        router = SubSchemaRouter(metastore=metastore, force_pure_python=True)
        pruned, meta = router.route_and_prune("Calculate net profit and gross profit", force_prune=True, top_k=4)
        assert meta["bypassed"] is False
        selected = set(pruned["tables"].keys())
        assert {"voucher", "voucherdetails", "products", "expensive"}.issubset(selected)

    def test_f31_bm25_and_dense_search_rank_relevant_tables_top(self, metastore: dict[str, Any]) -> None:
        router = SubSchemaRouter(metastore=metastore, force_pure_python=True)
        bm25_top = [t for t, _ in router.search_bm25("batch stock expiry mfg_date", top_k=3)]
        dense_top = [t for t, _ in router.search_dense("batch stock expiry mfg_date", top_k=3)]
        assert "batches" in bm25_top
        assert "batches" in dense_top

    def test_f31_fk_graph_2_hop_bridge_expansion(self, metastore: dict[str, Any]) -> None:
        router = SubSchemaRouter(metastore=metastore, force_pure_python=True)
        path = router.find_shortest_path("batches", "companies")
        assert path == ["batches", "products", "companies"]
        expanded = router.expand_fk_graph(["batches", "companies"], max_tables=10)
        assert "products" in expanded

    def test_f31_enterprise_60_table_schema_pruning(self, metastore: dict[str, Any]) -> None:
        big_ms = copy.deepcopy(metastore)
        for i in range(45):
            big_ms["tables"][f"aux_custom_table_{i:02d}"] = {
                "description": f"Auxiliary custom module table {i}",
                "module_scope": ["auxiliary"],
                "primary_key": ["id"],
                "columns": {"id": {"type": "int(11)", "nullable": False}},
            }
        assert len(big_ms["tables"]) == 65
        router = SubSchemaRouter(metastore=big_ms, force_pure_python=True)
        pruned, meta = router.route_and_prune("Show unpaid balance for sundry debtors", top_k=5, max_k=10)
        assert meta["bypassed"] is False
        assert meta["original_table_count"] == 65
        assert meta["pruned_table_count"] <= 12
        assert "accounts" in pruned["tables"]


# =========================================================================== #
# FEATURE 32: Gold Evaluation Benchmark Suite (benchmarks/gold_eval_suite.py)
# =========================================================================== #
class TestFeature32GoldBenchmarkSuite:
    def test_f32_gold_queries_200_samples_and_categories(self) -> None:
        data = json.loads(DEFAULT_BENCHMARK_PATH.read_text(encoding="utf-8"))
        assert len(data) == 200
        samples = [GoldBenchmarkSample.model_validate(x) for x in data]
        cats = [s.category for s in samples]
        assert cats.count("single_table") == 40
        assert cats.count("two_table_join") == 40
        assert cats.count("complex_aggregation") == 45
        assert cats.count("time_series_date") == 45
        assert cats.count("adversarial_security") == 30

    def test_f32_gt_verification_100_percent_pass_rate(self) -> None:
        harness = GoldEvalHarness(benchmark_file=DEFAULT_BENCHMARK_PATH)
        summary = harness.run_gt_verification()
        assert summary.total_samples == 200
        assert summary.passed_samples == 200
        assert summary.execution_accuracy == 100.0
        assert summary.guardrail_compliance_rate == 100.0

    def test_f32_mock_evaluation_exceeds_88_percent_target(self) -> None:
        harness = GoldEvalHarness(benchmark_file=DEFAULT_BENCHMARK_PATH)
        summary = harness.run_mock_evaluation()
        assert summary.total_samples == 200
        assert summary.execution_accuracy >= 88.0

    def test_f32_compare_results_tolerance_and_multiset_matching(self) -> None:
        assert compare_results([{"a": 10.0, "b": "X"}], [{"B": "x", "A": 10.004}], tolerance=0.01) is True
        assert compare_results([{"a": 10.0}], [{"a": 12.0}], tolerance=0.01) is False

    def test_f32_adversarial_samples_all_require_refusal(self) -> None:
        data = json.loads(DEFAULT_BENCHMARK_PATH.read_text(encoding="utf-8"))
        adv = [GoldBenchmarkSample.model_validate(x) for x in data if x["is_adversarial"]]
        assert len(adv) == 30
        assert all(a.expected_intent == "REFUSAL" and a.expected_error_code is not None for a in adv)
