"""Constrained JSON inference client (vLLM / Ollama / OpenAI / Gemini / OpenRouter).

Sends structured generation requests with `ReportPlanSchema` JSON Schema constraints,
parses the output, and automatically verifies generated SQL queries through the
SQLGlot AST Security Gate.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Optional

import httpx

from app.config import Settings, get_settings
from app.inference.prompts import build_inference_prompt
from app.metastore.loader import infer_modules, load_metastore
from app.schemas.report_plan import ReportPlanSchema, report_plan_json_schema
from app.security.guardrails import SQLSecurityViolation, validate_and_sanitize_sql

logger = logging.getLogger(__name__)


# HTTP status codes that represent transient failures eligible for retry
TRANSIENT_HTTP_STATUS_CODES: tuple[int, ...] = (429, 500, 502, 503, 504)


# --- Domain Exceptions for Clean Error Normalization ---

class LLMError(Exception):
    """Base exception for LLM inference failures."""

    def __init__(
        self,
        message: str,
        provider: str = "unknown",
        status_code: Optional[int] = None,
        original_error: Optional[Exception] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.status_code = status_code
        self.original_error = original_error


# Alias for backward compatibility
LLMClientError = LLMError


class LLMTimeoutError(LLMError):
    """Raised when an inference call times out."""
    pass


class LLMConnectionError(LLMError):
    """Raised when connection to LLM host fails."""
    pass


class LLMAuthenticationError(LLMError):
    """Raised on HTTP 401 or 403 authorization failures."""
    pass


class LLMRateLimitError(LLMError):
    """Raised on HTTP 429 Too Many Requests."""
    pass


class LLMResponseError(LLMError):
    """Raised when the provider returns a non-2xx status code or invalid payload."""
    pass


# --- Multi-Tier JSON Decoding Pipeline ---

def extract_json_payload(raw_text: str) -> dict[str, Any]:
    """Extract JSON object from raw LLM output with 5-tier error recovery."""
    trimmed = (raw_text or "").strip()
    if not trimmed:
        return {
            "thought_process": "Empty response received from LLM.",
            "tables_used": [],
            "metrics_applied": [],
            "sql_query": None,
            "suggested_visualization": "table",
            "error": "LLM returned an empty response.",
        }

    # Tier 1: Direct JSON parsing
    try:
        res = json.loads(trimmed)
        if isinstance(res, dict):
            return res
    except Exception:
        pass

    # Tier 2: Extract from markdown code fences anywhere in output
    match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", trimmed, re.DOTALL)
    if match:
        try:
            res = json.loads(match.group(1))
            if isinstance(res, dict):
                return res
        except Exception:
            pass

    # Tier 3: Slice outermost braces { ... }
    start = trimmed.find("{")
    end = trimmed.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = trimmed[start : end + 1]
        try:
            res = json.loads(candidate)
            if isinstance(res, dict):
                return res
        except Exception:
            # Tier 4: Clean common formatting quirks (trailing commas before } or ])
            repaired = re.sub(r",\s*([}\]])", r"\1", candidate)
            try:
                res = json.loads(repaired)
                if isinstance(res, dict):
                    return res
            except Exception:
                pass

    # Tier 5: Fallback synthesis for unstructured refusal or error
    logger.warning("Failed to decode LLM response as JSON. Synthesizing refusal fallback. Raw: %s", trimmed[:200])
    return {
        "thought_process": "Model emitted unstructured text response.",
        "tables_used": [],
        "metrics_applied": [],
        "sql_query": None,
        "suggested_visualization": "table",
        "error": f"Failed to parse model output as JSON: {trimmed[:300]}",
    }


def normalize_invoice_extraction(data: dict[str, Any]) -> dict[str, Any]:
    """Map common multimodal party nesting to the capture schema."""
    normalized = dict(data)
    document_type = normalized.get("document_type")
    if isinstance(document_type, dict):
        normalized["document_type"] = document_type.get("value")

    for party, name_key, gstin_key in (
        ("supplier", "supplier_name", "supplier_gstin"),
        ("customer", "customer_name", "customer_gstin"),
    ):
        details = normalized.get(party)
        if not isinstance(details, dict):
            continue
        if name_key not in normalized and isinstance(details.get("name"), dict):
            normalized[name_key] = details["name"]
        if gstin_key not in normalized and isinstance(details.get("gst_in"), dict):
            normalized[gstin_key] = details["gst_in"]
    normalized.pop("supplier", None)
    normalized.pop("customer", None)
    return normalized


class LLMClient:
    """Client for generating constrained ReportPlanSchema responses from an LLM."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        metastore: Optional[dict[str, Any]] = None,
        http_client: Optional[httpx.Client] = None,
        async_http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.metastore = metastore or load_metastore(self.settings.metastore_path)
        self.json_schema = report_plan_json_schema(strict=True)
        self._http_client = http_client
        self._async_http_client = async_http_client
        self.last_token_usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    async def generate_invoice_extraction_async(
        self,
        *,
        tenant_id: str,
        file_name: str,
        content_type: str,
        source_base64: str,
        document_type: str | None = None,
    ) -> dict[str, Any]:
        """Extract a normalized GST-first invoice object without touching ERP data."""
        system_prompt = (
            "You extract invoice data for an ERP review workflow. Return JSON only. "
            "Never invent values: use null and add a warning when a value is unreadable. "
            "Preserve numeric values as numbers. Supported document types are purchase, sales, "
            "expense, credit-note, and debit-note. Include confidence from 0 to 1 for every "
            "field using {value, confidence, corrected:false, warning:null}."
        )
        user_text = (
            f"Tenant: {tenant_id}\nFile: {file_name}\nContent type: {content_type}\n"
            f"Requested document type: {document_type or 'classify'}\n"
            "Extract document_type, supplier/customer names and GSTINs, invoice_number, "
            "invoice_date, taxable_value, cgst_amount, sgst_amount, igst_amount, total_tax, "
            "total_amount, lines[{product,product_id,hsn_code,quantity,unit_price,discount,"
            "gst_rate,cgst_rate,sgst_rate,igst_rate,total}], and warnings."
        )
        endpoint, payload, headers = self._prepare_request(system_prompt, user_text)
        messages = payload.get("messages", [])
        if messages:
            messages[-1] = {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": f"data:{content_type};base64,{source_base64}"}},
                ],
            }
            payload["messages"] = messages
        payload["max_tokens"] = 4096
        payload["response_format"] = {"type": "json_object"}
        if self.settings.llm_provider == "vllm":
            payload.pop("guided_json", None)
        timeout_s = self.settings.llm_timeout_s or 60.0
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=timeout_s) as client:
                    response = await client.post(endpoint, json=payload, headers=headers)
                    response.raise_for_status()
                content = self._extract_usage_and_content(response.json())
                data = extract_json_payload(content)
                break
            except Exception as exc:
                normalized = self._normalize_http_exception(exc)
                retryable = isinstance(normalized, (LLMTimeoutError, LLMConnectionError, LLMRateLimitError)) or (
                    isinstance(normalized, LLMResponseError)
                    and normalized.status_code in TRANSIENT_HTTP_STATUS_CODES
                )
                if retryable and attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise normalized from exc
        if not isinstance(data, dict):
            raise LLMResponseError("Invoice extraction response was not a JSON object", provider=self.settings.llm_provider)
        return normalize_invoice_extraction(data)

    def _build_payload_openai(
        self,
        system_prompt: str,
        user_prompt: str,
        history: Optional[list[dict[str, str]]] = None,
    ) -> dict[str, Any]:
        """Format payload for OpenAI-compatible providers (vLLM, OpenAI, Gemini, OpenRouter)."""
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_prompt})

        payload: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": 2048,
            "response_format": {"type": "json_object"},
        }
        # vLLM accepts guided_json directly; other providers reject it with 400 Bad Request
        if self.settings.llm_provider == "vllm":
            payload["guided_json"] = self.json_schema
        return payload

    def _build_payload_ollama(
        self,
        system_prompt: str,
        user_prompt: str,
        history: Optional[list[dict[str, str]]] = None,
    ) -> dict[str, Any]:
        """Format payload for native Ollama API."""
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_prompt})

        return {
            "model": self.settings.llm_model,
            "messages": messages,
            "options": {"temperature": 0.0},
            "format": self.json_schema,
            "stream": False,
        }

    def _prepare_request(
        self,
        system_prompt: str,
        user_prompt: str,
        history: Optional[list[dict[str, str]]] = None,
    ) -> tuple[str, dict[str, Any], dict[str, str]]:
        """Resolve endpoint, payload, and headers for the active provider."""
        provider = self.settings.llm_provider
        base_url = (self.settings.llm_base_url or "http://localhost:8000/v1").rstrip("/")

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.settings.llm_api_key:
            headers["Authorization"] = f"Bearer {self.settings.llm_api_key}"

        # OpenRouter-specific ranking/analytics headers
        if provider == "openrouter":
            headers["HTTP-Referer"] = "https://erp-ai-reporting.local"
            headers["X-Title"] = "ERP Ledger Reporting Agent"

        if provider == "ollama" and "/v1" not in base_url:
            endpoint = f"{base_url}/api/chat"
            payload = self._build_payload_ollama(system_prompt, user_prompt, history=history)
        else:
            endpoint = f"{base_url}/chat/completions"
            payload = self._build_payload_openai(system_prompt, user_prompt, history=history)

        return endpoint, payload, headers

    def _extract_usage_and_content(self, res_json: dict[str, Any]) -> str:
        """Extract token usage and content string from provider response."""
        if not isinstance(res_json, dict):
            raise LLMResponseError(
                f"Expected JSON dictionary from provider, got {type(res_json).__name__}: {res_json}",
                provider=self.settings.llm_provider,
            )
        # Track usage
        if "usage" in res_json and isinstance(res_json["usage"], dict):
            u = res_json["usage"]
            prompt_t = int(u.get("prompt_tokens", 0) or 0)
            comp_t = int(u.get("completion_tokens", 0) or 0)
            total_t = int(u.get("total_tokens", prompt_t + comp_t) or (prompt_t + comp_t))
            self.last_token_usage = {
                "prompt_tokens": prompt_t,
                "completion_tokens": comp_t,
                "total_tokens": total_t,
            }
        elif "prompt_eval_count" in res_json:
            prompt_t = int(res_json.get("prompt_eval_count", 0) or 0)
            comp_t = int(res_json.get("eval_count", 0) or 0)
            self.last_token_usage = {
                "prompt_tokens": prompt_t,
                "completion_tokens": comp_t,
                "total_tokens": prompt_t + comp_t,
            }
        else:
            self.last_token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        # Extract content
        if "choices" in res_json and len(res_json["choices"]) > 0:
            msg = res_json["choices"][0].get("message", {})
            if isinstance(msg, dict):
                return msg.get("content", "") or ""
            return str(msg)
        elif "message" in res_json and isinstance(res_json["message"], dict):
            return res_json["message"].get("content", "") or ""
        else:
            raise LLMResponseError(
                f"Unrecognized LLM response format: {res_json}",
                provider=self.settings.llm_provider,
            )

    def _normalize_http_exception(self, exc: Exception) -> LLMError:
        """Normalize httpx exceptions into domain exceptions."""
        provider = self.settings.llm_provider
        if isinstance(exc, (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
            return LLMTimeoutError(
                f"Request to LLM provider '{provider}' timed out: {exc}",
                provider=provider,
                original_error=exc,
            )
        if isinstance(exc, (httpx.ConnectError, httpx.NetworkError)):
            return LLMConnectionError(
                f"Failed to connect to LLM provider '{provider}': {exc}",
                provider=provider,
                original_error=exc,
            )
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            text = exc.response.text[:200]
            if code in (401, 403):
                return LLMAuthenticationError(
                    f"Authentication failed for provider '{provider}' (HTTP {code}): {text}",
                    provider=provider,
                    status_code=code,
                    original_error=exc,
                )
            if code == 429:
                return LLMRateLimitError(
                    f"Rate limit exceeded for provider '{provider}' (HTTP 429): {text}",
                    provider=provider,
                    status_code=code,
                    original_error=exc,
                )
            return LLMResponseError(
                f"LLM provider '{provider}' returned HTTP {code}: {text}",
                provider=provider,
                status_code=code,
                original_error=exc,
            )
        if isinstance(exc, LLMError):
            return exc
        return LLMError(
            f"Unexpected error with LLM provider '{provider}': {exc}",
            provider=provider,
            original_error=exc,
        )

    async def generate_report_plan_async(
        self,
        user_query: str,
        tenant_id: str = "default",
        history: Optional[list[dict[str, str]]] = None,
        error_context: Optional[dict[str, Any]] = None,
        modules: Optional[list[str]] = None,
        metastore: Optional[dict[str, Any]] = None,
        validate_ast: bool = True,
        raise_on_error: bool = False,
    ) -> ReportPlanSchema:
        """Asynchronously generate a ReportPlanSchema with retry and error normalization."""
        effective_metastore = metastore or self.metastore
        target_modules = modules or infer_modules(user_query)
        system_prompt, user_prompt = build_inference_prompt(
            user_question=user_query,
            metastore=effective_metastore,
            modules=target_modules,
            error_context=error_context,
        )

        endpoint, payload, headers = self._prepare_request(system_prompt, user_prompt, history=history)
        timeout_s = self.settings.llm_timeout_s or 60.0
        max_retries = max(0, self.settings.llm_max_retries)

        res_json = None
        last_exc: Optional[Exception] = None

        for attempt in range(max_retries + 1):
            try:
                if self._async_http_client:
                    resp = await self._async_http_client.post(endpoint, json=payload, headers=headers, timeout=timeout_s)
                else:
                    async with httpx.AsyncClient(timeout=timeout_s) as client:
                        resp = await client.post(endpoint, json=payload, headers=headers)
                resp.raise_for_status()
                res_json = resp.json()
                break
            except Exception as exc:
                last_exc = exc
                if attempt < max_retries and isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError)):
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in TRANSIENT_HTTP_STATUS_CODES:
                        break  # Do not retry non-transient HTTP errors (e.g. 400, 401, 403, 404, 422)
                    backoff = 0.5 * (2 ** attempt)
                    logger.warning("LLM call attempt %d failed (%s). Retrying in %.1fs...", attempt + 1, exc, backoff)
                    await asyncio.sleep(backoff)
                else:
                    break

        if res_json is None:
            norm_err = self._normalize_http_exception(last_exc or Exception("Unknown error"))
            if raise_on_error:
                raise norm_err
            return ReportPlanSchema(
                thought_process=f"Inference call failed: {norm_err.message}",
                tables_used=[],
                metrics_applied=[],
                sql_query=None,
                suggested_visualization="table",
                error=f"LLM Provider Error [{self.settings.llm_provider}]: {norm_err.message}",
            )

        try:
            raw_content = self._extract_usage_and_content(res_json)
        except LLMResponseError as exc:
            if raise_on_error:
                raise
            return ReportPlanSchema(
                thought_process=f"Inference call failed: {exc.message}",
                tables_used=[],
                metrics_applied=[],
                sql_query=None,
                suggested_visualization="table",
                error=f"LLM Provider Error [{self.settings.llm_provider}]: {exc.message}",
            )
        data = extract_json_payload(raw_content)
        try:
            plan = ReportPlanSchema.model_validate(data)
        except Exception as exc:
            logger.warning("Failed to validate ReportPlanSchema: %s. Emitting refusal fallback.", exc)
            return ReportPlanSchema(
                thought_process="Model output failed Pydantic schema validation.",
                tables_used=[],
                metrics_applied=[],
                sql_query=None,
                suggested_visualization="table",
                error=f"Schema validation error: {exc}",
            )

        if validate_ast and plan.sql_query:
            try:
                sanitized_sql = validate_and_sanitize_sql(
                    raw_sql=plan.sql_query,
                    metastore=effective_metastore,
                    dialect=self.settings.sql_dialect,
                    max_rows=self.settings.max_row_limit,
                )
                plan.sql_query = sanitized_sql
            except SQLSecurityViolation as exc:
                logger.warning("LLM emitted SQL that violated AST security gate: %s", exc)
                plan.sql_query = None
                plan.error = f"Security Gate Blocked Query: {exc.message} ({exc.code})"

        return plan

    def generate_report_plan(
        self,
        user_query: str,
        tenant_id: str = "default",
        history: Optional[list[dict[str, str]]] = None,
        error_context: Optional[dict[str, Any]] = None,
        modules: Optional[list[str]] = None,
        metastore: Optional[dict[str, Any]] = None,
        validate_ast: bool = True,
        raise_on_error: bool = False,
    ) -> ReportPlanSchema:
        """Synchronously generate a ReportPlanSchema with retry and error normalization."""
        effective_metastore = metastore or self.metastore
        target_modules = modules or infer_modules(user_query)
        system_prompt, user_prompt = build_inference_prompt(
            user_question=user_query,
            metastore=effective_metastore,
            modules=target_modules,
            error_context=error_context,
        )

        endpoint, payload, headers = self._prepare_request(system_prompt, user_prompt, history=history)
        timeout_s = self.settings.llm_timeout_s or 60.0
        max_retries = max(0, self.settings.llm_max_retries)

        res_json = None
        last_exc: Optional[Exception] = None

        for attempt in range(max_retries + 1):
            try:
                if self._http_client:
                    resp = self._http_client.post(endpoint, json=payload, headers=headers, timeout=timeout_s)
                else:
                    with httpx.Client(timeout=timeout_s) as client:
                        resp = client.post(endpoint, json=payload, headers=headers)
                resp.raise_for_status()
                res_json = resp.json()
                break
            except Exception as exc:
                last_exc = exc
                if attempt < max_retries and isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError)):
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in TRANSIENT_HTTP_STATUS_CODES:
                        break  # Do not retry non-transient HTTP errors (e.g. 400, 401, 403, 404, 422)
                    backoff = 0.5 * (2 ** attempt)
                    logger.warning("LLM call attempt %d failed (%s). Retrying in %.1fs...", attempt + 1, exc, backoff)
                    time.sleep(backoff)
                else:
                    break

        if res_json is None:
            norm_err = self._normalize_http_exception(last_exc or Exception("Unknown error"))
            if raise_on_error:
                raise norm_err
            return ReportPlanSchema(
                thought_process=f"Inference call failed: {norm_err.message}",
                tables_used=[],
                metrics_applied=[],
                sql_query=None,
                suggested_visualization="table",
                error=f"LLM Provider Error [{self.settings.llm_provider}]: {norm_err.message}",
            )

        try:
            raw_content = self._extract_usage_and_content(res_json)
        except LLMResponseError as exc:
            if raise_on_error:
                raise
            return ReportPlanSchema(
                thought_process=f"Inference call failed: {exc.message}",
                tables_used=[],
                metrics_applied=[],
                sql_query=None,
                suggested_visualization="table",
                error=f"LLM Provider Error [{self.settings.llm_provider}]: {exc.message}",
            )
        data = extract_json_payload(raw_content)
        try:
            plan = ReportPlanSchema.model_validate(data)
        except Exception as exc:
            logger.warning("Failed to validate ReportPlanSchema: %s. Emitting refusal fallback.", exc)
            return ReportPlanSchema(
                thought_process="Model output failed Pydantic schema validation.",
                tables_used=[],
                metrics_applied=[],
                sql_query=None,
                suggested_visualization="table",
                error=f"Schema validation error: {exc}",
            )

        if validate_ast and plan.sql_query:
            try:
                sanitized_sql = validate_and_sanitize_sql(
                    raw_sql=plan.sql_query,
                    metastore=effective_metastore,
                    dialect=self.settings.sql_dialect,
                    max_rows=self.settings.max_row_limit,
                )
                plan.sql_query = sanitized_sql
            except SQLSecurityViolation as exc:
                logger.warning("LLM emitted SQL that violated AST security gate: %s", exc)
                plan.sql_query = None
                plan.error = f"Security Gate Blocked Query: {exc.message} ({exc.code})"

        return plan

    # Backward compatibility aliases
    async def generate_plan_async(
        self,
        question: str,
        modules: Optional[list[str]] = None,
        validate_ast: bool = True,
        error_context: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> ReportPlanSchema:
        return await self.generate_report_plan_async(
            user_query=question,
            modules=modules,
            validate_ast=validate_ast,
            error_context=error_context,
            **kwargs,
        )

    def generate_plan(
        self,
        question: str,
        modules: Optional[list[str]] = None,
        validate_ast: bool = True,
        error_context: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> ReportPlanSchema:
        return self.generate_report_plan(
            user_query=question,
            modules=modules,
            validate_ast=validate_ast,
            error_context=error_context,
            **kwargs,
        )
