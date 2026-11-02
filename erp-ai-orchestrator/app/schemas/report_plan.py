"""Constrained inference contract: the only shape the LLM is allowed to emit."""

from __future__ import annotations

import copy
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Visualization = Literal["table", "bar", "line", "pie", "kpi_card"]


class ReportPlanSchema(BaseModel):
    """Structured Text-to-SQL plan returned by the model.

    Invariant: exactly one of ``sql_query`` / ``error`` is populated. Refusals
    (mutation requests, restricted tables, out-of-schema asks) set
    ``sql_query = None`` and explain why in ``error``.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    thought_process: str = Field(..., min_length=1, max_length=4000,
                                 description="Brief reasoning: tables, joins, metrics and filters chosen.")
    tables_used: List[str] = Field(default_factory=list, description="Physical tables referenced by sql_query.")
    metrics_applied: List[str] = Field(default_factory=list, description="Keys of calculated_metrics used verbatim.")
    sql_query: Optional[str] = Field(default=None, description="Single read-only MySQL SELECT/UNION/CTE statement.")
    suggested_visualization: Visualization = Field(default="table")
    error: Optional[str] = Field(default=None, description="Populated only when the request is refused.")

    @field_validator("sql_query", "error", mode="before")
    @classmethod
    def _blank_to_none(cls, v: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("tables_used", "metrics_applied")
    @classmethod
    def _dedupe(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(s.strip() for s in v if s and s.strip()))

    @model_validator(mode="after")
    def _exactly_one_outcome(self) -> "ReportPlanSchema":
        if self.sql_query is None and self.error is None:
            raise ValueError("Either sql_query or error must be provided.")
        if self.sql_query is not None and self.error is not None:
            raise ValueError("sql_query and error are mutually exclusive.")
        return self

    @property
    def is_refusal(self) -> bool:
        return self.sql_query is None


def report_plan_json_schema(strict: bool = True) -> dict[str, Any]:
    """JSON Schema for constrained decoding (OpenAI strict / vLLM guided_json / Ollama format).

    Strict mode requires every property to be listed in ``required`` and
    ``additionalProperties: false``; nullable fields remain expressible via ``anyOf``.
    """
    schema = copy.deepcopy(ReportPlanSchema.model_json_schema())
    if strict:
        schema["required"] = list(schema["properties"].keys())
        schema["additionalProperties"] = False
        for prop in schema["properties"].values():
            prop.pop("default", None)
    return schema
