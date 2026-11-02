"""ChatML and ReportPlanSchema Dataset Record Validator."""

from __future__ import annotations

import json
import logging
from typing import Any

import sqlglot
from sqlglot import exp

logger = logging.getLogger(__name__)

VALID_ROLES = {"system", "user", "assistant"}
REQUIRED_PLAN_FIELDS = {
    "thought_process",
    "tables_used",
    "metrics_applied",
    "sql_query",
    "suggested_visualization",
    "error",
}


def validate_single_record(record: dict[str, Any]) -> tuple[bool, str | None]:
    """Validate a single ChatML dataset line against formatting and semantic invariants."""
    if not isinstance(record, dict) or "messages" not in record:
        return False, "Record must be a JSON object with a 'messages' list"

    messages = record["messages"]
    if not isinstance(messages, list) or len(messages) < 3:
        return False, f"Expected at least 3 messages (system, user, assistant), got {len(messages)}"

    roles = [m.get("role") for m in messages if isinstance(m, dict)]
    if roles != ["system", "user", "assistant"]:
        return False, f"Messages must have roles ['system', 'user', 'assistant'], got {roles}"

    system_content = messages[0].get("content", "")
    user_content = messages[1].get("content", "")
    assistant_content = messages[2].get("content", "")

    if not system_content or not isinstance(system_content, str):
        return False, "System message content must be a non-empty string"
    if not user_content or not isinstance(user_content, str):
        return False, "User message content must be a non-empty string"
    if not assistant_content or not isinstance(assistant_content, str):
        return False, "Assistant message content must be a non-empty string"

    # Assistant content must be valid JSON matching ReportPlanSchema
    try:
        plan_dict = json.loads(assistant_content)
    except Exception as exc:
        return False, f"Assistant content is not valid JSON: {exc}"

    if not isinstance(plan_dict, dict):
        return False, "Assistant content JSON must be a dictionary"

    missing = REQUIRED_PLAN_FIELDS - set(plan_dict.keys())
    if missing:
        return False, f"Assistant plan missing required fields: {sorted(missing)}"

    sql_query = plan_dict.get("sql_query")
    error = plan_dict.get("error")

    # Mutual Exclusivity Invariant: exactly one of sql_query or error is non-null
    if (sql_query is None and error is None) or (sql_query is not None and error is not None):
        return False, "Mutual exclusivity violated: exactly one of 'sql_query' or 'error' must be non-null"

    # If valid query, validate SQL syntax and table alignment via sqlglot
    if sql_query is not None:
        try:
            parsed = sqlglot.parse_one(sql_query, read="mysql")
            if not isinstance(parsed, (exp.Select, exp.Union)):
                return False, f"SQL query must be a SELECT or UNION statement, got {type(parsed).__name__}"

            # Cross-validate tables_used with tables in SQL AST
            ast_tables = {t.name.lower() for t in parsed.find_all(exp.Table) if t.name}
            plan_tables = {t.lower() for t in plan_dict.get("tables_used", []) if isinstance(t, str)}
            if ast_tables != plan_tables:
                return (
                    False,
                    f"Table mismatch: SQL AST references {sorted(ast_tables)} but tables_used has {sorted(plan_tables)}",
                )
        except Exception as exc:
            return False, f"SQL syntax or semantic error in query '{sql_query}': {exc}"

    return True, None


def validate_dataset_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate a batch of ChatML records and return a comprehensive report."""
    total = len(records)
    valid_count = 0
    errors: list[dict[str, Any]] = []

    for idx, rec in enumerate(records):
        is_valid, err_msg = validate_single_record(rec)
        if is_valid:
            valid_count += 1
        else:
            errors.append({"index": idx, "error": err_msg})

    return {
        "total_records": total,
        "valid_records": valid_count,
        "invalid_records": len(errors),
        "is_valid": len(errors) == 0,
        "errors": errors[:20],  # Truncate for readability
    }
