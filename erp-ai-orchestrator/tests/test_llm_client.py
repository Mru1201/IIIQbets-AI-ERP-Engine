"""Unit test suite for Milestone 1: Universal Multi-Provider LLM Client.

Covers:
1. Provider configuration factory defaults, environment overrides, and resolution
2. Synchronous and asynchronous execution across all 5 providers (vLLM, Ollama, OpenAI, Gemini, OpenRouter)
3. 5-Tier JSON extraction, schema validation, and invariant enforcement
4. Network exceptions, timeouts, retries, and error normalization
5. AST Security Gate integration
6. Dynamic prompt building, all 8 canonical metrics, business rules, and error context formatting
"""

from __future__ import annotations

import asyncio
import json
import os
import pytest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from pydantic import ValidationError

from app.config import PROVIDER_DEFAULTS, Settings, get_settings
from app.inference.llm_client import (
    LLMAuthenticationError,
    LLMClient,
    LLMClientError,
    LLMConnectionError,
    LLMError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
    extract_json_payload,
)
from app.inference.prompts import (
    build_inference_prompt,
    build_system_prompt,
    format_business_rules_for_prompt,
    format_data_caveats_for_prompt,
    format_error_context_for_prompt,
    format_joins_for_prompt,
    format_metrics_for_prompt,
    format_schema_for_prompt,
    generate_correction_hint,
)
from app.metastore.loader import load_metastore
from app.schemas.report_plan import ReportPlanSchema


# --- Fixtures ---

@pytest.fixture
def metastore():
    settings = Settings()
    return load_metastore(settings.metastore_path)


@pytest.fixture
def valid_plan_dict():
    return {
        "thought_process": "Calculating total revenue from voucher table.",
        "tables_used": ["voucher"],
        "metrics_applied": ["total_sales_revenue"],
        "sql_query": "SELECT SUM(TotalAmount) AS revenue FROM voucher WHERE TransactionType = 'Sales'",
        "suggested_visualization": "kpi_card",
        "error": None,
    }


@pytest.fixture
def valid_refusal_dict():
    return {
        "thought_process": "Refusing write operation.",
        "tables_used": [],
        "metrics_applied": [],
        "sql_query": None,
        "suggested_visualization": "table",
        "error": "Data modifications are strictly forbidden.",
    }


# =====================================================================
# Test Group 1: Provider Configuration Factory & Defaults
# =====================================================================

class TestProviderConfiguration:
    def test_default_settings_is_vllm(self):
        s = Settings()
        assert s.llm_provider == "vllm"
        assert s.llm_base_url == "http://localhost:8000/v1"
        assert s.llm_model == "Qwen/Qwen2.5-Coder-14B-Instruct"
        assert s.llm_timeout_s == 60.0
        assert s.llm_max_retries == 1

    def test_ollama_provider_configuration(self):
        s = Settings(llm_provider="ollama")
        assert s.llm_provider == "ollama"
        assert s.llm_base_url == "http://localhost:11434/v1"
        assert s.llm_model == "qwen2.5-coder:7b"
        assert s.llm_timeout_s == 60.0
        assert s.llm_api_key == "ollama"

    def test_openai_provider_configuration(self):
        s = Settings(llm_provider="openai", openai_api_key="sk-test-openai")
        assert s.llm_provider == "openai"
        assert s.llm_base_url == "https://api.openai.com/v1"
        assert s.llm_model == "gpt-4o-mini"
        assert s.llm_timeout_s == 30.0
        assert s.llm_api_key == "sk-test-openai"

    def test_gemini_provider_configuration(self):
        s = Settings(llm_provider="gemini", gemini_api_key="gem-test-key")
        assert s.llm_provider == "gemini"
        assert s.llm_base_url == "https://generativelanguage.googleapis.com/v1beta/openai/"
        assert s.llm_model == "gemini-2.0-flash"
        assert s.llm_timeout_s == 30.0
        assert s.llm_api_key == "gem-test-key"

    def test_openrouter_provider_configuration(self):
        s = Settings(llm_provider="openrouter", openrouter_api_key="or-test-key")
        assert s.llm_provider == "openrouter"
        assert s.llm_base_url == "https://openrouter.ai/api/v1"
        assert s.llm_model == "qwen/qwen-2.5-coder-32b-instruct"
        assert s.llm_timeout_s == 45.0
        assert s.llm_api_key == "or-test-key"

    def test_unsupported_provider_raises_validation_error(self):
        with pytest.raises(ValidationError):
            Settings(llm_provider="anthropic")

    def test_trailing_slash_normalization(self, metastore):
        s = Settings(llm_provider="vllm", llm_base_url="http://localhost:8000/v1/")
        client = LLMClient(settings=s, metastore=metastore)
        endpoint, _, _ = client._prepare_request("sys", "user")
        assert endpoint == "http://localhost:8000/v1/chat/completions"

    def test_environment_variable_precedence(self):
        with patch.dict(os.environ, {
            "ERP_LLM_PROVIDER": "openrouter",
            "ERP_LLM_BASE_URL": "https://custom.router/v1",
            "ERP_LLM_MODEL": "custom-model",
            "ERP_LLM_TIMEOUT_S": "99.0",
        }):
            s = Settings()
            assert s.llm_provider == "openrouter"
            assert s.llm_base_url == "https://custom.router/v1"
            assert s.llm_model == "custom-model"
            assert s.llm_timeout_s == 99.0

    def test_alias_choices_env_vars(self):
        # Test standard non-prefixed env vars
        with patch.dict(os.environ, {
            "GEMINI_API_KEY": "direct-gemini-key",
            "OPENROUTER_API_KEY": "direct-openrouter-key",
            "OPENAI_API_KEY": "direct-openai-key",
            "LLM_API_KEY": "direct-llm-key",
        }, clear=False):
            s = Settings(llm_provider="gemini")
            assert s.gemini_api_key == "direct-gemini-key"
            assert s.openrouter_api_key == "direct-openrouter-key"
            assert s.openai_api_key == "direct-openai-key"
            assert s.llm_api_key == "direct-llm-key"

    def test_get_provider_config_helper(self):
        s = Settings(llm_provider="vllm")
        cfg_gemini = s.get_provider_config("gemini")
        assert cfg_gemini["provider"] == "gemini"
        assert cfg_gemini["base_url"] == "https://generativelanguage.googleapis.com/v1beta/openai/"
        assert cfg_gemini["model"] == "gemini-2.0-flash"
        assert cfg_gemini["timeout_s"] == 30.0


# =====================================================================
# Test Group 2: Multi-Provider Execution (Mocked httpx transport)
# =====================================================================

class TestMultiProviderExecution:
    def test_sync_generate_report_plan_vllm(self, metastore, valid_plan_dict):
        captured_request = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_request["url"] = str(request.url)
            captured_request["headers"] = dict(request.headers)
            captured_request["json"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": json.dumps(valid_plan_dict)}}],
                    "usage": {"prompt_tokens": 120, "completion_tokens": 45, "total_tokens": 165},
                },
            )

        s = Settings(llm_provider="vllm")
        client = LLMClient(
            settings=s,
            metastore=metastore,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        plan = client.generate_report_plan(user_query="What is total revenue?")
        assert plan.is_refusal is False
        assert "LIMIT 1000" in plan.sql_query
        assert "guided_json" in captured_request["json"]
        assert captured_request["json"]["temperature"] == 0.0
        assert captured_request["json"]["max_tokens"] == 2048
        assert client.last_token_usage["total_tokens"] == 165

    @pytest.mark.anyio
    async def test_async_generate_report_plan_vllm(self, metastore, valid_plan_dict):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": json.dumps(valid_plan_dict)}}],
                    "usage": {"prompt_tokens": 80, "completion_tokens": 30, "total_tokens": 110},
                },
            )

        s = Settings(llm_provider="vllm")
        client = LLMClient(
            settings=s,
            metastore=metastore,
            async_http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        plan = await client.generate_report_plan_async(user_query="What is total revenue?")
        assert plan.is_refusal is False
        assert client.last_token_usage["total_tokens"] == 110

    def test_sync_generate_report_plan_ollama_native(self, metastore, valid_plan_dict):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["json"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "message": {"content": json.dumps(valid_plan_dict)},
                    "prompt_eval_count": 95,
                    "eval_count": 40,
                },
            )

        s = Settings(llm_provider="ollama", llm_base_url="http://localhost:11434")
        client = LLMClient(
            settings=s,
            metastore=metastore,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        plan = client.generate_report_plan(user_query="Total sales")
        assert plan.is_refusal is False
        assert captured["url"] == "http://localhost:11434/api/chat"
        assert "format" in captured["json"]
        assert client.last_token_usage["total_tokens"] == 135

    def test_sync_generate_report_plan_ollama_v1(self, metastore, valid_plan_dict):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": json.dumps(valid_plan_dict)}}],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
                },
            )

        s = Settings(llm_provider="ollama", llm_base_url="http://localhost:11434/v1")
        client = LLMClient(
            settings=s,
            metastore=metastore,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        plan = client.generate_report_plan(user_query="Total sales")
        assert plan.is_refusal is False
        assert captured["url"] == "http://localhost:11434/v1/chat/completions"

    def test_sync_generate_report_plan_openai(self, metastore, valid_plan_dict):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = dict(request.headers)
            captured["json"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps(valid_plan_dict)}}]},
            )

        s = Settings(llm_provider="openai", openai_api_key="sk-proj-12345")
        client = LLMClient(
            settings=s,
            metastore=metastore,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        plan = client.generate_report_plan(user_query="Sales")
        assert plan.is_refusal is False
        assert captured["headers"]["authorization"] == "Bearer sk-proj-12345"
        assert "guided_json" not in captured["json"]

    def test_sync_generate_report_plan_gemini(self, metastore, valid_plan_dict):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["json"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps(valid_plan_dict)}}]},
            )

        s = Settings(llm_provider="gemini", gemini_api_key="AIzaSyTest")
        client = LLMClient(
            settings=s,
            metastore=metastore,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        plan = client.generate_report_plan(user_query="Revenue")
        assert plan.is_refusal is False
        assert captured["url"] == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        assert "guided_json" not in captured["json"]

    def test_sync_generate_report_plan_openrouter(self, metastore, valid_plan_dict):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = dict(request.headers)
            captured["json"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps(valid_plan_dict)}}]},
            )

        s = Settings(llm_provider="openrouter", openrouter_api_key="sk-or-v1-test")
        client = LLMClient(
            settings=s,
            metastore=metastore,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        plan = client.generate_report_plan(user_query="Net Profit")
        assert plan.is_refusal is False
        assert captured["headers"]["http-referer"] == "https://erp-ai-reporting.local"
        assert captured["headers"]["x-title"] == "ERP Ledger Reporting Agent"
        assert "guided_json" not in captured["json"]

    @pytest.mark.anyio
    async def test_async_generate_report_plan_ollama_native(self, metastore, valid_plan_dict):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "message": {"content": json.dumps(valid_plan_dict)},
                    "prompt_eval_count": 50,
                    "eval_count": 25,
                },
            )

        s = Settings(llm_provider="ollama", llm_base_url="http://localhost:11434")
        client = LLMClient(
            settings=s,
            metastore=metastore,
            async_http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        plan = await client.generate_report_plan_async(user_query="Total sales")
        assert plan.is_refusal is False
        assert client.last_token_usage["total_tokens"] == 75

    @pytest.mark.anyio
    async def test_async_generate_report_plan_openai(self, metastore, valid_plan_dict):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = dict(request.headers)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps(valid_plan_dict)}}]},
            )

        s = Settings(llm_provider="openai", openai_api_key="sk-test-async")
        client = LLMClient(
            settings=s,
            metastore=metastore,
            async_http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        plan = await client.generate_report_plan_async(user_query="Sales")
        assert plan.is_refusal is False
        assert captured["headers"]["authorization"] == "Bearer sk-test-async"

    @pytest.mark.anyio
    async def test_async_generate_report_plan_gemini(self, metastore, valid_plan_dict):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps(valid_plan_dict)}}]},
            )

        s = Settings(llm_provider="gemini", gemini_api_key="gem-async")
        client = LLMClient(
            settings=s,
            metastore=metastore,
            async_http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        plan = await client.generate_report_plan_async(user_query="Revenue")
        assert plan.is_refusal is False
        assert "generativelanguage.googleapis.com" in captured["url"]

    @pytest.mark.anyio
    async def test_async_generate_report_plan_openrouter(self, metastore, valid_plan_dict):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = dict(request.headers)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps(valid_plan_dict)}}]},
            )

        s = Settings(llm_provider="openrouter", openrouter_api_key="or-async")
        client = LLMClient(
            settings=s,
            metastore=metastore,
            async_http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        plan = await client.generate_report_plan_async(user_query="Expenses")
        assert plan.is_refusal is False
        assert captured["headers"]["http-referer"] == "https://erp-ai-reporting.local"

    def test_auth_header_omitted_when_key_is_none(self, metastore, valid_plan_dict):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = dict(request.headers)
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(valid_plan_dict)}}]})

        s = Settings(llm_provider="vllm", llm_api_key=None)
        client = LLMClient(
            settings=s,
            metastore=metastore,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        client.generate_report_plan(user_query="Test")
        assert "authorization" not in captured["headers"]

    def test_backward_compatibility_aliases(self, metastore, valid_plan_dict):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(valid_plan_dict)}}]})

        s = Settings(llm_provider="vllm")
        client = LLMClient(
            settings=s,
            metastore=metastore,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
            async_http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        # Sync alias
        sync_plan = client.generate_plan(question="What is revenue?")
        assert sync_plan.is_refusal is False

        # Async alias
        async_plan = asyncio.run(client.generate_plan_async(question="What is revenue?"))
        assert async_plan.is_refusal is False


# =====================================================================
# Test Group 3: 5-Tier JSON Extraction & Schema Parsing
# =====================================================================

class TestJsonParsingAndSchema:
    def test_extract_clean_json_tier1(self, valid_plan_dict):
        raw = json.dumps(valid_plan_dict)
        parsed = extract_json_payload(raw)
        assert parsed["thought_process"] == valid_plan_dict["thought_process"]
        assert parsed["sql_query"] == valid_plan_dict["sql_query"]

    def test_extract_markdown_fenced_json_tier2(self, valid_plan_dict):
        raw = f"Here is your report:\n```json\n{json.dumps(valid_plan_dict, indent=2)}\n```\nHope this helps!"
        parsed = extract_json_payload(raw)
        assert parsed["thought_process"] == valid_plan_dict["thought_process"]

    def test_extract_outer_brace_boundary_tier3(self, valid_plan_dict):
        raw = f"Note: check schema.\n{json.dumps(valid_plan_dict)}\nEnd of message."
        parsed = extract_json_payload(raw)
        assert parsed["thought_process"] == valid_plan_dict["thought_process"]

    def test_extract_trailing_comma_repair_tier4(self):
        malformed = """{
            "thought_process": "Calculated with trailing commas",
            "tables_used": ["voucher",],
            "metrics_applied": [],
            "sql_query": "SELECT * FROM voucher",
            "suggested_visualization": "table",
            "error": null,
        }"""
        parsed = extract_json_payload(malformed)
        assert parsed["thought_process"] == "Calculated with trailing commas"
        assert parsed["tables_used"] == ["voucher"]

    def test_extract_plain_text_refusal_tier5(self):
        plain_text = "I cannot fulfill this request as confidential payroll data cannot be queried."
        parsed = extract_json_payload(plain_text)
        assert parsed["sql_query"] is None
        assert "confidential payroll data" in parsed["error"]
        plan = ReportPlanSchema.model_validate(parsed)
        assert plan.is_refusal is True

    def test_extract_empty_or_whitespace(self):
        parsed = extract_json_payload("   \n\t  ")
        assert parsed["sql_query"] is None
        assert "empty response" in parsed["error"].lower()
        plan = ReportPlanSchema.model_validate(parsed)
        assert plan.is_refusal is True

    def test_valid_sql_plan_schema(self, valid_plan_dict):
        plan = ReportPlanSchema.model_validate(valid_plan_dict)
        assert plan.is_refusal is False
        assert plan.error is None
        assert plan.suggested_visualization == "kpi_card"

    def test_valid_refusal_plan_schema(self, valid_refusal_dict):
        plan = ReportPlanSchema.model_validate(valid_refusal_dict)
        assert plan.is_refusal is True
        assert plan.sql_query is None
        assert plan.error is not None

    def test_schema_invariants_both_populated_raises(self, valid_plan_dict):
        invalid = dict(valid_plan_dict)
        invalid["error"] = "Error message"
        with pytest.raises(ValueError, match="mutually exclusive"):
            ReportPlanSchema.model_validate(invalid)

    def test_schema_invariants_both_none_raises(self, valid_plan_dict):
        invalid = dict(valid_plan_dict)
        invalid["sql_query"] = None
        invalid["error"] = None
        with pytest.raises(ValueError, match="Either sql_query or error must be provided"):
            ReportPlanSchema.model_validate(invalid)

    def test_schema_field_deduplication(self, valid_plan_dict):
        dup = dict(valid_plan_dict)
        dup["tables_used"] = ["voucher", "voucher", "voucherdetails"]
        dup["metrics_applied"] = ["gross_profit", "gross_profit"]
        plan = ReportPlanSchema.model_validate(dup)
        assert plan.tables_used == ["voucher", "voucherdetails"]
        assert plan.metrics_applied == ["gross_profit"]

    def test_schema_whitespace_normalization(self, valid_plan_dict):
        ws = dict(valid_plan_dict)
        ws["sql_query"] = "   "
        ws["error"] = "Valid error"
        plan = ReportPlanSchema.model_validate(ws)
        assert plan.sql_query is None
        assert plan.is_refusal is True


# =====================================================================
# Test Group 4: Error Handling, Timeouts, and Retries
# =====================================================================

class TestErrorHandlingAndResilience:
    def test_timeout_raises_llm_timeout_error(self, metastore):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("Mocked read timeout")

        s = Settings(llm_provider="vllm", llm_max_retries=0)
        client = LLMClient(
            settings=s,
            metastore=metastore,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        with pytest.raises(LLMTimeoutError):
            client.generate_report_plan("Query", raise_on_error=True)

    def test_timeout_returns_refusal_plan(self, metastore):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("Mocked read timeout")

        s = Settings(llm_provider="vllm", llm_max_retries=0)
        client = LLMClient(
            settings=s,
            metastore=metastore,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        plan = client.generate_report_plan("Query", raise_on_error=False)
        assert plan.is_refusal is True
        assert "timed out" in plan.error.lower()

    def test_connection_error_raises_llm_connection_error(self, metastore):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        s = Settings(llm_provider="ollama", llm_max_retries=0)
        client = LLMClient(
            settings=s,
            metastore=metastore,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        with pytest.raises(LLMConnectionError):
            client.generate_report_plan("Query", raise_on_error=True)

    def test_connection_error_returns_refusal_plan(self, metastore):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        s = Settings(llm_provider="ollama", llm_max_retries=0)
        client = LLMClient(
            settings=s,
            metastore=metastore,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        plan = client.generate_report_plan("Query", raise_on_error=False)
        assert plan.is_refusal is True
        assert "failed to connect" in plan.error.lower()

    def test_http_401_raises_llm_auth_error(self, metastore):
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(401, text="Unauthorized: Invalid API key")

        s = Settings(llm_provider="openai", llm_max_retries=2)
        client = LLMClient(
            settings=s,
            metastore=metastore,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        with pytest.raises(LLMAuthenticationError) as exc_info:
            client.generate_report_plan("Query", raise_on_error=True)
        assert exc_info.value.status_code == 401
        assert attempts == 1  # Auth errors should NOT retry

    def test_http_429_raises_llm_rate_limit_error(self, metastore):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text="Rate limit exceeded")

        s = Settings(llm_provider="gemini", llm_max_retries=0)
        client = LLMClient(
            settings=s,
            metastore=metastore,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        with pytest.raises(LLMRateLimitError):
            client.generate_report_plan("Query", raise_on_error=True)

    def test_http_500_raises_llm_response_error(self, metastore):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Internal Server Error")

        s = Settings(llm_provider="openrouter", llm_max_retries=0)
        client = LLMClient(
            settings=s,
            metastore=metastore,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        with pytest.raises(LLMResponseError):
            client.generate_report_plan("Query", raise_on_error=True)

    def test_transient_retry_succeeds_on_second_attempt(self, metastore, valid_plan_dict):
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ReadTimeout("Transient read timeout")
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(valid_plan_dict)}}]})

        s = Settings(llm_provider="vllm", llm_max_retries=1)
        client = LLMClient(
            settings=s,
            metastore=metastore,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        plan = client.generate_report_plan("Revenue")
        assert plan.is_refusal is False
        assert call_count == 2

    def test_unrecognized_response_envelope_handled(self, metastore):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"unexpected_key": 123})

        s = Settings(llm_provider="vllm", llm_max_retries=0)
        client = LLMClient(
            settings=s,
            metastore=metastore,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        plan = client.generate_report_plan("Query", raise_on_error=False)
        assert plan.is_refusal is True
        assert "unrecognized" in plan.error.lower()

    @pytest.mark.anyio
    async def test_async_timeout_returns_refusal_plan(self, metastore):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("Mocked async read timeout")

        s = Settings(llm_provider="vllm", llm_max_retries=0)
        client = LLMClient(
            settings=s,
            metastore=metastore,
            async_http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        plan = await client.generate_report_plan_async("What is the sales revenue?", raise_on_error=False)
        assert isinstance(plan, ReportPlanSchema)
        assert plan.is_refusal is True
        assert plan.sql_query is None
        assert plan.error is not None
        assert "timed out" in plan.error.lower()
        assert "vllm" in plan.error.lower()

    @pytest.mark.anyio
    async def test_async_timeout_raises_llm_timeout_error(self, metastore):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("Mocked async read timeout")

        s = Settings(llm_provider="vllm", llm_max_retries=0)
        client = LLMClient(
            settings=s,
            metastore=metastore,
            async_http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        with pytest.raises(LLMTimeoutError) as exc_info:
            await client.generate_report_plan_async("Query", raise_on_error=True)
        assert "timed out" in str(exc_info.value).lower()
        assert exc_info.value.provider == "vllm"

    @pytest.mark.anyio
    async def test_async_connection_error_returns_refusal_plan(self, metastore):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused to host")

        s = Settings(llm_provider="ollama", llm_max_retries=0)
        client = LLMClient(
            settings=s,
            metastore=metastore,
            async_http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        plan = await client.generate_report_plan_async("Query", raise_on_error=False)
        assert isinstance(plan, ReportPlanSchema)
        assert plan.is_refusal is True
        assert plan.sql_query is None
        assert plan.error is not None
        assert "failed to connect" in plan.error.lower()
        assert "ollama" in plan.error.lower()

    @pytest.mark.anyio
    async def test_async_connection_error_raises_llm_connection_error(self, metastore):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        s = Settings(llm_provider="ollama", llm_max_retries=0)
        client = LLMClient(
            settings=s,
            metastore=metastore,
            async_http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        with pytest.raises(LLMConnectionError) as exc_info:
            await client.generate_report_plan_async("Query", raise_on_error=True)
        assert "failed to connect" in str(exc_info.value).lower()

    @pytest.mark.anyio
    async def test_async_http_401_raises_llm_auth_error(self, metastore):
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(401, text="Unauthorized: Invalid API key")

        s = Settings(llm_provider="openai", llm_max_retries=2)
        client = LLMClient(
            settings=s,
            metastore=metastore,
            async_http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        with pytest.raises(LLMAuthenticationError) as exc_info:
            await client.generate_report_plan_async("Query", raise_on_error=True)
        assert exc_info.value.status_code == 401
        assert "401" in str(exc_info.value)
        assert attempts == 1  # Crucial: Auth errors must NOT retry!

    @pytest.mark.anyio
    async def test_async_http_401_returns_refusal_plan(self, metastore):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="Unauthorized: Invalid API key")

        s = Settings(llm_provider="openai", llm_max_retries=0)
        client = LLMClient(
            settings=s,
            metastore=metastore,
            async_http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        plan = await client.generate_report_plan_async("Query", raise_on_error=False)
        assert plan.is_refusal is True
        assert "401" in plan.error

    @pytest.mark.anyio
    async def test_async_http_429_raises_llm_rate_limit_error(self, metastore):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text="Rate limit exceeded")

        s = Settings(llm_provider="gemini", llm_max_retries=0)
        client = LLMClient(
            settings=s,
            metastore=metastore,
            async_http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        with pytest.raises(LLMRateLimitError) as exc_info:
            await client.generate_report_plan_async("Query", raise_on_error=True)
        assert exc_info.value.status_code == 429

    @pytest.mark.anyio
    async def test_async_http_500_raises_llm_response_error(self, metastore):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Internal Server Error")

        s = Settings(llm_provider="openrouter", llm_max_retries=0)
        client = LLMClient(
            settings=s,
            metastore=metastore,
            async_http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        with pytest.raises(LLMResponseError) as exc_info:
            await client.generate_report_plan_async("Query", raise_on_error=True)
        assert exc_info.value.status_code == 500

    @pytest.mark.anyio
    async def test_async_http_500_returns_refusal_plan(self, metastore):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Internal Server Error")

        s = Settings(llm_provider="openrouter", llm_max_retries=0)
        client = LLMClient(
            settings=s,
            metastore=metastore,
            async_http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        plan = await client.generate_report_plan_async("Query", raise_on_error=False)
        assert plan.is_refusal is True
        assert "500" in plan.error

    @pytest.mark.anyio
    async def test_async_transient_retry_succeeds_on_second_attempt(self, metastore, valid_plan_dict):
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ReadTimeout("Transient async read timeout")
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps(valid_plan_dict)}}]},
            )

        s = Settings(llm_provider="vllm", llm_max_retries=1)
        client = LLMClient(
            settings=s,
            metastore=metastore,
            async_http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        plan = await client.generate_report_plan_async("Revenue")
        assert plan.is_refusal is False
        assert call_count == 2

    @pytest.mark.anyio
    async def test_async_unrecognized_response_envelope_handled(self, metastore):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"unexpected_key": 123})

        s = Settings(llm_provider="vllm", llm_max_retries=0)
        client = LLMClient(
            settings=s,
            metastore=metastore,
            async_http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        plan = await client.generate_report_plan_async("Query", raise_on_error=False)
        assert plan.is_refusal is True
        assert "unrecognized" in plan.error.lower()



# =====================================================================
# Test Group 5: AST Security Gate Integration
# =====================================================================

class TestAstSecurityGateIntegration:
    def test_ast_gate_sanitizes_valid_query(self, metastore):
        plan_dict = {
            "thought_process": "Valid query without limit.",
            "tables_used": ["voucher"],
            "metrics_applied": [],
            "sql_query": "SELECT VoucherID, Date, TotalAmount FROM voucher",
            "suggested_visualization": "table",
            "error": None,
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(plan_dict)}}]})

        client = LLMClient(
            metastore=metastore,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        plan = client.generate_report_plan("List vouchers")
        assert plan.is_refusal is False
        assert "LIMIT 1000" in plan.sql_query

    def test_ast_gate_blocks_forbidden_statement(self, metastore):
        plan_dict = {
            "thought_process": "Attempting drop table.",
            "tables_used": ["accounts"],
            "metrics_applied": [],
            "sql_query": "DROP TABLE accounts",
            "suggested_visualization": "table",
            "error": None,
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(plan_dict)}}]})

        client = LLMClient(
            metastore=metastore,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        plan = client.generate_report_plan("Drop accounts")
        assert plan.is_refusal is True
        assert plan.sql_query is None
        assert "Security Gate Blocked Query" in plan.error
        assert "FORBIDDEN_STATEMENT" in plan.error

    def test_ast_gate_blocks_restricted_table(self, metastore):
        plan_dict = {
            "thought_process": "Accessing credentials.",
            "tables_used": ["gsp_credentials"],
            "metrics_applied": [],
            "sql_query": "SELECT * FROM gsp_credentials",
            "suggested_visualization": "table",
            "error": None,
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(plan_dict)}}]})

        client = LLMClient(
            metastore=metastore,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        plan = client.generate_report_plan("Read credentials")
        assert plan.is_refusal is True
        assert plan.sql_query is None
        assert "RESTRICTED_TABLE" in plan.error

    def test_ast_gate_bypass_flag(self, metastore):
        plan_dict = {
            "thought_process": "Bypassing AST gate.",
            "tables_used": [],
            "metrics_applied": [],
            "sql_query": "SELECT 1 + 1",
            "suggested_visualization": "table",
            "error": None,
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(plan_dict)}}]})

        client = LLMClient(
            metastore=metastore,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        plan = client.generate_report_plan("Math", validate_ast=False)
        assert plan.sql_query == "SELECT 1 + 1"

    @pytest.mark.anyio
    async def test_async_ast_gate_sanitizes_valid_query(self, metastore):
        plan_dict = {
            "thought_process": "Valid query without limit.",
            "tables_used": ["voucher"],
            "metrics_applied": [],
            "sql_query": "SELECT VoucherID, Date, TotalAmount FROM voucher",
            "suggested_visualization": "table",
            "error": None,
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(plan_dict)}}]})

        client = LLMClient(
            metastore=metastore,
            async_http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        plan = await client.generate_report_plan_async("List vouchers")
        assert plan.is_refusal is False
        assert "LIMIT 1000" in plan.sql_query

    @pytest.mark.anyio
    async def test_async_ast_gate_blocks_forbidden_statement(self, metastore):
        plan_dict = {
            "thought_process": "Attempting drop table voucher.",
            "tables_used": ["voucher"],
            "metrics_applied": [],
            "sql_query": "DROP TABLE voucher",
            "suggested_visualization": "table",
            "error": None,
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps(plan_dict)}}]},
            )

        s = Settings(llm_provider="vllm", llm_max_retries=0)
        client = LLMClient(
            settings=s,
            metastore=metastore,
            async_http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        plan = await client.generate_report_plan_async("Drop the voucher table")
        assert plan.is_refusal is True
        assert plan.sql_query is None
        assert plan.error is not None
        assert "Security Gate Blocked Query" in plan.error
        assert "FORBIDDEN_STATEMENT" in plan.error

    @pytest.mark.anyio
    async def test_async_ast_gate_blocks_restricted_table(self, metastore):
        plan_dict = {
            "thought_process": "Accessing credentials.",
            "tables_used": ["gsp_credentials"],
            "metrics_applied": [],
            "sql_query": "SELECT * FROM gsp_credentials",
            "suggested_visualization": "table",
            "error": None,
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(plan_dict)}}]})

        client = LLMClient(
            metastore=metastore,
            async_http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        plan = await client.generate_report_plan_async("Read credentials")
        assert plan.is_refusal is True
        assert plan.sql_query is None
        assert "RESTRICTED_TABLE" in plan.error

    @pytest.mark.anyio
    async def test_async_ast_gate_bypass_flag(self, metastore):
        plan_dict = {
            "thought_process": "Bypassing AST gate in async mode.",
            "tables_used": [],
            "metrics_applied": [],
            "sql_query": "SELECT 1 + 1",
            "suggested_visualization": "table",
            "error": None,
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(plan_dict)}}]})

        client = LLMClient(
            metastore=metastore,
            async_http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        plan = await client.generate_report_plan_async("Math", validate_ast=False)
        assert plan.sql_query == "SELECT 1 + 1"


# =====================================================================
# Test Group 6: Dynamic Prompt Builder & Self-Correction Context
# =====================================================================

class TestPromptBuilder:
    def test_format_schema_includes_tables_and_fks(self, metastore):
        text = format_schema_for_prompt(metastore)
        assert "### Table: `voucher`" in text
        assert "### Table: `accounts`" in text
        assert "Declared FKs:" in text
        assert "CAST HINT:" in text or "CAVEAT:" in text

    def test_format_joins_categorizes_fks_and_logical(self, metastore):
        text = format_joins_for_prompt(metastore)
        assert "Declared Physical Foreign Keys:" in text
        assert "Logical Domain Relationships:" in text
        assert "voucherdetails.voucher_id = voucher.VoucherID" in text

    def test_format_metrics_includes_all_eight_canonical_metrics(self, metastore):
        text = format_metrics_for_prompt(metastore)
        expected_metrics = [
            "total_sales_revenue",
            "cost_of_goods_sold",
            "total_expenses",
            "gross_profit",
            "net_profit",
            "party_unpaid_balance",
            "total_purchases",
            "batch_stock_on_hand",
        ]
        for m in expected_metrics:
            assert f"#### Metric: `{m}`" in text

    def test_format_business_rules_renders_domain_rules(self, metastore):
        text = format_business_rules_for_prompt(metastore)
        assert "stock transfer" in text.lower() or "sales revenue" in text.lower()
        assert "indian fy" in text.lower() or "inr" in text.lower()

    def test_format_data_caveats_renders_all_caveats(self, metastore):
        text = format_data_caveats_for_prompt(metastore)
        assert "decimal" in text.lower() or "varchar" in text.lower()
        assert "`accounts`" in text

    def test_generate_correction_hint(self):
        h_table = generate_correction_hint("UNKNOWN_TABLE", "Table 'foo' not in schema")
        assert "does not exist in the tenant metastore" in h_table

        h_col = generate_correction_hint("UNKNOWN_COLUMN", "Unknown column 'Date'")
        assert "Back-quote reserved words" in h_col

        h_stmt = generate_correction_hint("FORBIDDEN_STATEMENT", "DROP is forbidden")
        assert "READ-ONLY" in h_stmt

        h_group = generate_correction_hint("DB_SYNTAX_ERROR", "Error 1055: ONLY_FULL_GROUP_BY")
        assert "ONLY_FULL_GROUP_BY" in h_group

    def test_format_error_context_for_prompt(self):
        err_ctx = {
            "attempt": 1,
            "max_attempts": 2,
            "previous_sql": "SELECT Date FROM voucher",
            "error_code": "UNKNOWN_COLUMN",
            "error_message": "Unknown column 'Date'",
        }
        text = format_error_context_for_prompt(err_ctx)
        assert "SELF-CORRECTION REQUIRED (Retry Attempt 1 of 2)" in text
        assert "SELECT Date FROM voucher" in text
        assert "UNKNOWN_COLUMN" in text
        assert "Actionable Correction Hint" in text

    def test_generate_correction_hint_none_inputs(self):
        """Verify that passing None or empty values does not raise AttributeError."""
        # Both None
        hint1 = generate_correction_hint(None, None)
        assert isinstance(hint1, str)
        assert len(hint1) > 0
        assert "Fix the specific SQL error" in hint1

        # Code provided, message None
        hint2 = generate_correction_hint("UNKNOWN_COLUMN", None)
        assert "Column does not exist" in hint2

        # Message provided, code None
        hint3 = generate_correction_hint(None, "Syntax error in SQL query")
        assert "Review SQL syntax" in hint3

        # Both empty strings
        hint4 = generate_correction_hint("", "")
        assert "Fix the specific SQL error" in hint4

    def test_format_error_context_for_prompt_none_and_missing_inputs(self):
        """Verify error context formatter handles explicit None and missing keys gracefully."""
        # Context with explicit None values
        err_ctx_none = {
            "attempt": None,
            "max_attempts": None,
            "previous_sql": None,
            "error_code": None,
            "error_category": None,
            "error_message": None,
            "hint": None,
        }
        text = format_error_context_for_prompt(err_ctx_none)
        assert "### SELF-CORRECTION REQUIRED (Retry Attempt 1 of 2):" in text
        assert "**Previous Failed SQL Query**:\nNone" in text
        assert "- **Error Classification**: `EXECUTION_ERROR`" in text
        assert "- **Error Details**: Unknown error encountered." in text
        assert "- **Actionable Correction Hint**: Fix the specific SQL error" in text

        # Empty dictionary context
        text_empty = format_error_context_for_prompt({})
        assert "### SELF-CORRECTION REQUIRED" in text_empty
        assert "`EXECUTION_ERROR`" in text_empty

    def test_build_system_prompt_includes_all_sections_and_invariants(self, metastore):
        prompt = build_system_prompt(metastore)
        assert "### SEMANTIC METASTORE SCHEMA:" in prompt
        assert "### ALLOWED JOINS & RELATIONSHIPS:" in prompt
        assert "### CALCULATED METRICS & CANONICAL FORMULAS:" in prompt
        assert "### BUSINESS RULES & ACCOUNTING CONVENTIONS:" in prompt
        assert "### DATA CAVEATS & TYPE CASTS:" in prompt
        assert "MUTUAL EXCLUSIVITY INVARIANT" in prompt

    def test_build_inference_prompt_with_and_without_error_context(self, metastore):
        # Without error context
        sys_prompt, user_prompt = build_inference_prompt(
            user_question="Show revenue",
            metastore=metastore,
        )
        assert "User Question:\nShow revenue" in user_prompt
        assert "SELF-CORRECTION REQUIRED" not in user_prompt

        # With error context
        err_ctx = {
            "attempt": 1,
            "previous_sql": "SELECT 1",
            "error_code": "PARSE_ERROR",
            "error_message": "Syntax error",
        }
        sys_prompt2, user_prompt2 = build_inference_prompt(
            user_question="Show revenue",
            metastore=metastore,
            error_context=err_ctx,
        )
        assert "SELF-CORRECTION REQUIRED" in user_prompt2
        assert "SELECT 1" in user_prompt2

    def test_build_inference_prompt_with_pruning(self, metastore):
        sys_prompt, _ = build_inference_prompt(
            user_question="Show my expenses",
            metastore=metastore,
            modules=["finance_expenses"],
        )
        assert "### Table: `expensive`" in sys_prompt
        # Non-related table should be pruned
        assert "### Table: `orders`" not in sys_prompt
