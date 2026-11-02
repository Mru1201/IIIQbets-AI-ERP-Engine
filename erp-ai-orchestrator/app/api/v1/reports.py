"""Report Query API Endpoint and 2-Step Self-Correction State Machine.

Pipeline:
1. Intake QueryRequest and authenticate via JWT / DEV_MODE.
2. Enter state machine loop: max_retries = 2 (up to 3 total attempts).
3. At each step:
   a. Call LLMClient.generate_report_plan_async (with history & error_context on retry).
   b. If refusal -> return refusal gracefully without retrying.
   c. If SQL generated -> validate using ASTGuardrail (fail-closed, inject LIMIT 1000).
      - If invalid -> record retry in retry_history, generate hint, retry loop.
   d. Execute validated SQL via DatabaseExecutor.execute_query.
      - If DB error -> record retry in retry_history, generate hint, retry loop.
   e. If execution succeeds -> break out of loop.
4. Derive dynamic UI Schema (column types, visualization, KPI cards).
5. Return QueryResponse with complete retry_history telemetry in execution_metadata.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.auth.jwt_handler import get_current_user, get_tenant_id
from app.config import Settings, get_settings
from app.connectors.executor import (
    ColumnNotFoundError,
    DatabaseConnectionError,
    DatabaseExecutionError,
    DatabaseExecutor,
    DatabaseSyntaxError,
    ExecutionResult,
    StatementTimeoutError,
    TableNotFoundError,
)
from app.inference.llm_client import LLMClient, LLMTimeoutError
from app.inference.prompts import generate_correction_hint
from app.metastore.loader import infer_modules, load_metastore
from app.schemas.api_models import (
    ExecutionMetadata,
    QueryRequest,
    QueryResponse,
    UISchema,
)
from app.schemas.report_plan import ReportPlanSchema
from app.security.guardrails import SQLSecurityViolation, validate_and_sanitize_sql
from app.api.v1.ui_deriver import derive_ui_schema
from app.metastore.router import SubSchemaRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["reports"])


# --- Dependency Providers ---

def get_db_executor(request: Request) -> DatabaseExecutor:
    """Resolve DatabaseExecutor from app state or create default instance."""
    if hasattr(request.app.state, "executor") and request.app.state.executor is not None:
        return request.app.state.executor
    if hasattr(request.app.state, "db_executor") and request.app.state.db_executor is not None:
        return request.app.state.db_executor
    return DatabaseExecutor()


def get_llm_client(request: Request) -> LLMClient:
    """Resolve LLMClient from app state or create default instance."""
    if hasattr(request.app.state, "llm_client") and request.app.state.llm_client is not None:
        return request.app.state.llm_client
    return LLMClient()


def get_metastore(request: Request) -> dict[str, Any]:
    """Resolve semantic metastore dictionary from app state or load from disk."""
    if hasattr(request.app.state, "metastore") and request.app.state.metastore is not None:
        return request.app.state.metastore
    return load_metastore()


# --- Query Endpoint Implementation ---

@router.post("/query", response_model=QueryResponse)
async def query_report(
    query_req: QueryRequest,
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),
    auth_tenant_id: str = Depends(get_tenant_id),
    db_executor: DatabaseExecutor = Depends(get_db_executor),
    llm_client: LLMClient = Depends(get_llm_client),
    metastore: dict[str, Any] = Depends(get_metastore),
    settings: Settings = Depends(get_settings),
) -> QueryResponse:
    """Execute natural language reporting query with 2-step self-correction state machine."""
    start_time = time.perf_counter()

    # Determine effective tenant ID: explicit auth header claims take priority over request payload
    effective_tenant_id = (
        current_user.get("tenant_id")
        if current_user.get("tenant_id") and current_user.get("tenant_id") != "default"
        else (query_req.tenant_id if query_req.tenant_id != "default" else current_user.get("tenant_id", "default"))
    )

    # Module scope resolution
    raw_modules = infer_modules(query_req.user_query)
    if query_req.module_scope and query_req.module_scope != "all":
        scope_map = {
            "finance": ["finance_expenses", "sales_purchase", "accounting"],
            "finance_expenses": ["finance_expenses", "accounting"],
            "expenses": ["finance_expenses", "accounting"],
            "sales": ["sales_purchase", "accounting"],
            "purchase": ["sales_purchase", "accounting"],
            "sales_purchase": ["sales_purchase", "accounting"],
            "tax": ["sales_purchase", "accounting"],
            "accounting": ["accounting"],
            "inventory": ["inventory", "accounting"],
            "orders": ["orders", "sales_purchase"],
        }
        target_modules = list(dict.fromkeys(scope_map.get(query_req.module_scope.lower(), [query_req.module_scope]) + raw_modules))
    else:
        target_modules = raw_modules or None

    # Sub-schema routing for enterprise schemas (>50 tables or when router is explicitly enabled)
    effective_metastore = metastore
    router_meta: Optional[dict[str, Any]] = None
    if settings.enable_schema_router or len(metastore.get("tables", {})) > settings.schema_router_threshold:
        router_instance = SubSchemaRouter(metastore=metastore, settings=settings)
        effective_metastore, router_meta = router_instance.route_and_prune(
            query=query_req.user_query,
            metastore=metastore,
        )
        logger.info(
            "SubSchemaRouter active: %d -> %d tables (latency: %.2f ms)",
            router_meta.get("original_table_count", 0),
            router_meta.get("pruned_table_count", 0),
            router_meta.get("latency_ms", 0.0),
        )

    max_retries = 2
    total_attempts = max_retries + 1

    retry_history: list[dict[str, Any]] = []
    error_context: Optional[dict[str, Any]] = None

    last_plan: Optional[ReportPlanSchema] = None
    verified_sql: Optional[str] = None
    execution_result: Optional[ExecutionResult] = None
    final_error: Optional[str] = None
    retries_used = 0

    # ----------------------------------------------------------------------- #
    # 2-Step Self-Correction State Machine Loop
    # ----------------------------------------------------------------------- #
    for attempt in range(1, total_attempts + 1):
        logger.info(
            "Executing reporting query attempt %d of %d (User query: %s)",
            attempt,
            total_attempts,
            query_req.user_query[:80],
        )

        # Step A: LLM Inference
        # (Pass validate_ast=False to preserve raw failed SQL for retry telemetry)
        try:
            plan = await llm_client.generate_report_plan_async(
                user_query=query_req.user_query,
                tenant_id=effective_tenant_id,
                history=query_req.history,
                error_context=error_context,
                modules=target_modules,
                metastore=effective_metastore,
                validate_ast=False,
                raise_on_error=False,
            )
            last_plan = plan
        except LLMTimeoutError as exc:
            logger.warning("LLM client timed out on attempt %d: %s", attempt, exc)
            retry_history.append({
                "attempt": attempt,
                "error_type": "TIMEOUT",
                "error_code": "LLMTimeoutError",
                "error_message": str(exc),
                "failed_sql": None,
                "correction_hint": "LLM generation timed out. Please simplify or optimize the query.",
                "timestamp": time.time(),
            })
            if attempt < total_attempts:
                error_context = {
                    "attempt": attempt + 1,
                    "max_attempts": total_attempts,
                    "previous_sql": None,
                    "error_code": "TIMEOUT",
                    "error_category": "TIMEOUT",
                    "error_message": str(exc),
                    "hint": "Ensure model responds promptly and compactly.",
                }
                continue
            final_error = f"LLM inference timeout: {exc}"
            break
        except Exception as exc:
            logger.error("Unexpected LLM client failure on attempt %d: %s", attempt, exc)
            retry_history.append({
                "attempt": attempt,
                "error_type": "LLM_INFERENCE_ERROR",
                "error_code": type(exc).__name__,
                "error_message": str(exc),
                "failed_sql": None,
                "correction_hint": "Please generate a valid ReportPlanSchema response.",
                "timestamp": time.time(),
            })
            if attempt < total_attempts:
                error_context = {
                    "attempt": attempt + 1,
                    "max_attempts": total_attempts,
                    "previous_sql": None,
                    "error_code": "LLM_ERROR",
                    "error_category": "INFERENCE_FAILURE",
                    "error_message": str(exc),
                    "hint": "Ensure model output conforms to ReportPlanSchema JSON format.",
                }
                continue
            final_error = f"LLM inference error: {exc}"
            break

        # Check for deliberate semantic refusal (e.g. mutation or prohibited tables)
        if plan.is_refusal:
            logger.info("Model emitted deliberate refusal on attempt %d: %s", attempt, plan.error)
            elapsed_ms = max(round((time.perf_counter() - start_time) * 1000.0, 2), 0.1)
            return QueryResponse(
                status="error",
                execution_metadata=ExecutionMetadata(
                    thought_process=plan.thought_process,
                    tables_used=plan.tables_used,
                    metrics_applied=plan.metrics_applied,
                    verified_sql=None,
                    retries_used=0,
                    retry_history=[],
                    latency_ms=elapsed_ms,
                    active_mode=db_executor.active_mode,
                    token_usage=getattr(llm_client, "last_token_usage", None),
                    session_id=query_req.session_id,
                ),
                ui_schema=UISchema(suggested_visualization="table", columns=[], kpis=[], filters=[]),
                data=[],
                error=plan.error or "Operation refused by security policy.",
                session_id=query_req.session_id,
            )

        # Step B: AST Guardrail Validation
        raw_sql = plan.sql_query
        try:
            sanitized_sql = validate_and_sanitize_sql(
                raw_sql=raw_sql,
                metastore=effective_metastore,
                dialect=settings.sql_dialect,
                max_rows=settings.max_row_limit,
            )
            verified_sql = sanitized_sql
        except SQLSecurityViolation as sec_exc:
            hint = generate_correction_hint(sec_exc.code, sec_exc.message)
            logger.warning("Attempt %d SQL failed AST Guardrail (%s): %s", attempt, sec_exc.code, sec_exc.message)

            if sec_exc.code in ("UNKNOWN_COLUMN", "UNKNOWN_TABLE"):
                err_type = "SCHEMA_ERROR"
            elif sec_exc.code in (
                "FORBIDDEN_STATEMENT",
                "FORBIDDEN_OPERATION",
                "RESTRICTED_TABLE",
                "EXECUTABLE_COMMENT",
                "MULTIPLE_STATEMENTS",
            ):
                err_type = "GUARDRAIL_VIOLATION"
            elif "PARSE_ERROR" in sec_exc.code:
                err_type = "SYNTAX_ERROR"
            else:
                err_type = "GUARDRAIL_VIOLATION"

            retry_history.append({
                "attempt": attempt,
                "error_type": err_type,
                "error_code": sec_exc.code,
                "error_message": sec_exc.message,
                "failed_sql": raw_sql,
                "correction_hint": hint,
                "timestamp": time.time(),
            })
            if attempt < total_attempts:
                error_context = {
                    "attempt": attempt + 1,
                    "max_attempts": total_attempts,
                    "previous_sql": raw_sql,
                    "error_code": sec_exc.code,
                    "error_category": err_type,
                    "error_message": sec_exc.message,
                    "hint": hint,
                }
                continue
            final_error = f"AST Guardrail violation: {sec_exc.message} ({sec_exc.code})"
            break

        # Step C: Database Query Execution
        try:
            db_res = await asyncio.to_thread(
                db_executor.execute_query,
                verified_sql,
                tenant_id=effective_tenant_id,
                timeout_ms=settings.db_statement_timeout_ms,
            )
            execution_result = db_res
            retries_used = attempt - 1
            logger.info("Attempt %d succeeded with %d rows returned in %.2fms", attempt, db_res.row_count, db_res.execution_time_ms)
            break  # Success! Exit loop.
        except DatabaseExecutionError as db_exc:
            if isinstance(db_exc, StatementTimeoutError):
                err_type = "TIMEOUT"
            elif isinstance(db_exc, DatabaseSyntaxError):
                err_type = "SYNTAX_ERROR"
            elif isinstance(db_exc, TableNotFoundError):
                err_type = "TABLE_NOT_FOUND"
            elif isinstance(db_exc, ColumnNotFoundError):
                err_type = "ColumnNotFoundError"
            elif isinstance(db_exc, DatabaseConnectionError):
                err_type = "CONNECTION_ERROR"
            else:
                err_type = db_exc.error_code or "DATABASE_ERROR"

            hint = generate_correction_hint(db_exc.error_code, db_exc.message)
            logger.warning("Attempt %d SQL execution failed (%s): %s", attempt, err_type, db_exc.message)
            retry_history.append({
                "attempt": attempt,
                "error_type": err_type,
                "error_code": db_exc.error_code,
                "error_message": db_exc.message,
                "failed_sql": verified_sql or raw_sql,
                "correction_hint": hint,
                "timestamp": time.time(),
            })
            if attempt < total_attempts:
                error_context = {
                    "attempt": attempt + 1,
                    "max_attempts": total_attempts,
                    "previous_sql": verified_sql or raw_sql,
                    "error_code": db_exc.error_code,
                    "error_category": err_type,
                    "error_message": db_exc.message,
                    "hint": hint,
                }
                continue
            if isinstance(db_exc, StatementTimeoutError):
                final_error = f"Database query execution timeout: {db_exc.message}"
            else:
                final_error = f"Database execution error: {db_exc.message}"
            break

    # ----------------------------------------------------------------------- #
    # Post-Loop Finalization & Dynamic UI Schema Derivation
    # ----------------------------------------------------------------------- #
    total_latency_ms = max(round((time.perf_counter() - start_time) * 1000.0, 2), 0.1)

    # 1. Successful execution
    if execution_result is not None:
        ui_schema = derive_ui_schema(
            columns=execution_result.columns,
            data=execution_result.rows,
            metrics_applied=last_plan.metrics_applied if last_plan else [],
            llm_suggestion=last_plan.suggested_visualization if last_plan else "table",
        )
        return QueryResponse(
            status="success",
            execution_metadata=ExecutionMetadata(
                thought_process=last_plan.thought_process if last_plan else "",
                tables_used=last_plan.tables_used if last_plan else [],
                metrics_applied=last_plan.metrics_applied if last_plan else [],
                verified_sql=verified_sql,
                retries_used=retries_used,
                retry_history=retry_history,
                latency_ms=total_latency_ms,
                active_mode=execution_result.source_mode or db_executor.active_mode,
                token_usage=getattr(llm_client, "last_token_usage", None),
                session_id=query_req.session_id,
            ),
            ui_schema=ui_schema,
            data=execution_result.rows,
            error=None,
            session_id=query_req.session_id,
        )

    # 2. Retries exhausted / unrecoverable failure
    return QueryResponse(
        status="error",
        execution_metadata=ExecutionMetadata(
            thought_process=last_plan.thought_process if last_plan else "Query execution failed after retries.",
            tables_used=last_plan.tables_used if last_plan else [],
            metrics_applied=last_plan.metrics_applied if last_plan else [],
            verified_sql=verified_sql,
            retries_used=len(retry_history),
            retry_history=retry_history,
            latency_ms=total_latency_ms,
            active_mode=db_executor.active_mode,
            token_usage=getattr(llm_client, "last_token_usage", None),
            session_id=query_req.session_id,
        ),
        ui_schema=UISchema(suggested_visualization="table", columns=[], kpis=[], filters=[]),
        data=[],
        error=final_error or "Query generation and execution failed after retries.",
        session_id=query_req.session_id,
    )
