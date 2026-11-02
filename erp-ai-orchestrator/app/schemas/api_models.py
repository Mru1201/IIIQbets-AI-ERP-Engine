"""Pydantic API models for the ERP Ledger Reporting endpoint.

Defines the contract between frontend clients (standalone SPA and host ERP
web component) and the FastAPI orchestrator.
"""

from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


# --- Visualization & Column Types ---

VisualizationType = Literal[
    "kpi_grid",
    "kpi_card",
    "bar_chart",
    "line_chart",
    "donut_chart",
    "table",
]

ColumnDataType = Literal[
    "string",
    "currency",
    "number",
    "date",
    "boolean",
]

ResponseStatus = Literal[
    "success",
    "partial",
    "error",
    "refused",
]


# --- Request Models ---

class QueryRequest(BaseModel):
    """Incoming natural language report request."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    tenant_id: str = Field(
        default="default",
        description="Tenant identifier for multi-tenant isolation.",
    )
    module_scope: str = Field(
        default="finance",
        description="Target business module scope: 'finance', 'sales', 'inventory', 'accounting', 'orders', or 'all'.",
    )

    @field_validator("module_scope", mode="before")
    @classmethod
    def _coerce_module_scope(cls, v: Any) -> str:
        if isinstance(v, list):
            return v[0] if v else "all"
        if v is None:
            return "all"
        return str(v)
    user_query: str = Field(
        ...,
        min_length=1,
        description="Natural language question or reporting prompt.",
        examples=["What is our total sales revenue and net profit for 2026?"],
    )
    session_id: Optional[str] = Field(
        default=None,
        description="Optional client session ID for conversation tracking.",
    )
    history: Optional[list[dict[str, str]]] = Field(
        default=None,
        description="Optional chat history message array for conversational follow-ups.",
    )
    filters: Optional[dict[str, Any]] = Field(
        default=None,
        description="Optional client pre-filters (e.g. date ranges, party filters).",
    )

    @field_validator("user_query")
    @classmethod
    def _validate_user_query(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("user_query must not be empty or whitespace only.")
        return trimmed


# --- UI Schema Models ---

class ColumnDef(BaseModel):
    """Schema definition for a data grid / chart column."""

    model_config = ConfigDict(extra="ignore")

    key: str = Field(..., description="Column key matching data row dictionary keys.")
    label: str = Field(..., description="Human-readable column header label.")
    type: ColumnDataType = Field(default="string", description="Inferred semantic data type.")
    align: Optional[Literal["left", "center", "right"]] = Field(
        default=None,
        description="Recommended text alignment in data grids.",
    )
    sortable: bool = Field(default=True, description="Whether the column is sortable.")


class KPICard(BaseModel):
    """Summary KPI metric card for Zone 1 of the Analytical List Page."""

    model_config = ConfigDict(extra="ignore")

    label: str = Field(..., description="KPI title or metric name.")
    value: str | float = Field(..., description="Formatted string or raw numeric value.")
    trend: Optional[Literal["positive", "negative", "neutral"]] = Field(
        default=None,
        description="Directional sentiment indicator.",
    )
    subtext: Optional[str] = Field(
        default=None,
        description="Secondary context or formula annotation.",
    )


class UISchema(BaseModel):
    """Dynamic UI metadata instructing the frontend how to render the result set."""

    model_config = ConfigDict(extra="ignore")

    suggested_visualization: VisualizationType = Field(
        default="table",
        description="Recommended primary view: 'kpi_grid', 'bar_chart', 'line_chart', 'donut_chart', or 'table'.",
    )
    columns: list[ColumnDef] = Field(
        default_factory=list,
        description="Ordered list of column definitions for tables and charts.",
    )
    kpis: list[KPICard] = Field(
        default_factory=list,
        description="Extracted summary KPI metric tiles.",
    )
    filters: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Active or extracted filter pill definitions.",
    )


# --- Execution Metadata & Retries ---

class RetryAttempt(BaseModel):
    """Full transparency telemetry record for a single failed self-correction step."""

    model_config = ConfigDict(extra="ignore")

    attempt: int = Field(..., description="1-indexed attempt number.")
    error_type: str = Field(..., description="Error classification: GUARDRAIL_VIOLATION, TIMEOUT, SYNTAX_ERROR, etc.")
    error_code: Optional[str] = Field(default=None, description="Granular error code.")
    error_message: str = Field(..., description="Sanitized error description.")
    failed_sql: Optional[str] = Field(default=None, description="The SQL query that failed validation or execution.")
    correction_hint: Optional[str] = Field(default=None, description="Diagnostic hint provided to LLM for retry.")
    timestamp: Optional[float] = Field(default=None, description="Unix timestamp of attempt failure.")


class ExecutionMetadata(BaseModel):
    """Agent execution telemetry and reasoning traces for Zone 3 Joule Copilot drawer."""

    model_config = ConfigDict(extra="ignore")

    thought_process: str = Field(
        default="",
        description="Model reasoning trace explaining query strategy, joins, and filters.",
    )
    tables_used: list[str] = Field(
        default_factory=list,
        description="Physical database tables referenced in the query.",
    )
    metrics_applied: list[str] = Field(
        default_factory=list,
        description="Canonical calculated metrics applied.",
    )
    verified_sql: Optional[str] = Field(
        default=None,
        description="AST-validated, sanitized, limit-injected SQL query.",
    )
    retries_used: int = Field(
        default=0,
        ge=0,
        description="Number of self-correction retries executed.",
    )
    retry_history: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Full transparency audit log of failed attempts and correction diffs.",
    )
    latency_ms: float = Field(
        default=0.0,
        description="Total round-trip pipeline execution latency in milliseconds.",
    )
    active_mode: str = Field(
        default="sqlite",
        description="Active database execution engine mode: 'live_mysql' or 'sqlite_fallback'.",
    )
    token_usage: Optional[dict[str, int]] = Field(
        default=None,
        description="Optional token counts (prompt, completion, total).",
    )
    session_id: Optional[str] = Field(
        default=None,
        description="Session ID associated with execution.",
    )


# --- Main Response Model ---

class QueryResponse(BaseModel):
    """Standardized response envelope returned by POST /api/v1/reports/query."""

    model_config = ConfigDict(extra="ignore")

    status: ResponseStatus = Field(
        default="success",
        description="Overall query status: 'success', 'partial', 'error', or 'refused'.",
    )
    execution_metadata: ExecutionMetadata = Field(
        ...,
        description="Reasoning traces, AST verification, and self-correction telemetry.",
    )
    ui_schema: UISchema = Field(
        ...,
        description="Inferred visualization recommendations, column types, and KPI cards.",
    )
    data: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Normalized tabular result set.",
    )
    error: Optional[str] = Field(
        default=None,
        description="User-facing error message or refusal reason.",
    )
    session_id: Optional[str] = Field(
        default=None,
        description="Echoed client session ID.",
    )
