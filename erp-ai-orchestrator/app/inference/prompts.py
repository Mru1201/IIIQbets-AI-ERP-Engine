"""Deterministic system and user prompt builders for Text-to-SQL inference.

Grounds the LLM strictly on the tenant metastore:
- Only whitelisted tables & columns are visible.
- Foreign keys and logical join paths are categorized and made explicit.
- All 8 canonical calculated metrics are codified with descriptions, expressions, filters, and canonical SQL.
- Authoritative accounting business rules and database caveats are strictly enforced.
- Self-correction error context injection formats previous invalid SQL, error classification, and specific hints for retries.
- Enforces strict ReportPlanSchema JSON output with the mutual exclusivity invariant.
"""

from __future__ import annotations

import datetime
import json
from typing import Any, Iterable, Optional

from app.metastore.loader import prune_metastore
from app.schemas.report_plan import report_plan_json_schema


def format_schema_for_prompt(metastore: dict[str, Any]) -> str:
    """Format tables, primary keys, declared foreign keys, columns, types, and casting caveats."""
    lines: list[str] = []
    tables = metastore.get("tables", {})
    for tname, tmeta in sorted(tables.items()):
        desc = tmeta.get("description", "")
        pk = ", ".join(tmeta.get("primary_key", [])) or "None"
        modules = ", ".join(tmeta.get("module_scope", []))

        # Collect declared foreign keys directly on table
        fks: list[str] = []
        for fk in tmeta.get("declared_foreign_keys", []):
            fks.append(f"{', '.join(fk['columns'])} -> {fk['ref_table']}({', '.join(fk['ref_columns'])})")
        fk_str = f" | Declared FKs: {'; '.join(fks)}" if fks else ""
        module_str = f" [Scope: {modules}]" if modules else ""

        lines.append(f"### Table: `{tname}` (PK: {pk}{fk_str}){module_str}")
        if desc:
            lines.append(f"Description: {desc}")
        lines.append("Columns:")
        for cname, cm in sorted(tmeta.get("columns", {}).items()):
            ctype = cm.get("type", "unknown")
            null_str = "NULL" if cm.get("nullable", True) else "NOT NULL"
            col_desc = cm.get("description", "")
            enums = cm.get("enum_values")
            known = cm.get("known_values")
            caveat = cm.get("caveat")
            cast_hint = cm.get("cast_hint")
            numeric_text = cm.get("numeric_text", False)

            extra: list[str] = []
            if enums:
                extra.append(f"enum={enums}")
            elif known:
                extra.append(f"values={known}")

            if caveat and cast_hint:
                extra.append(f"CAVEAT: {caveat} (use {cast_hint})")
            elif caveat:
                extra.append(f"CAVEAT: {caveat}")
            elif cast_hint:
                extra.append(f"CAST HINT: {cast_hint}")
            elif numeric_text:
                extra.append(f"CAST HINT: CAST({tname}.{cname} AS DECIMAL(15,2))")

            if col_desc:
                extra.append(col_desc)

            extra_str = f" -- {'; '.join(extra)}" if extra else ""
            lines.append(f"  - `{cname}` ({ctype}, {null_str}){extra_str}")
        lines.append("")
    return "\n".join(lines).strip()


def format_joins_for_prompt(metastore: dict[str, Any]) -> str:
    """Format allowed joins, categorizing declared physical FKs and logical domain relationships."""
    joins = metastore.get("allowed_joins", [])
    if not joins:
        return "None"

    fk_lines: list[str] = []
    logical_lines: list[str] = []

    for j in joins:
        on_clause = j.get("on", "")
        rel = j.get("relationship", "logical")
        card = j.get("cardinality", "")
        desc = j.get("description", "")
        caveat = j.get("caveat", "")
        hint = j.get("alias_hint") or j.get("join_type_hint")

        card_str = f" ({card})" if card else ""
        hint_str = f" [HINT: {hint}]" if hint else ""
        caveat_str = f" [CAVEAT: {caveat}]" if caveat else ""
        entry = f"- `{on_clause}`{card_str}: {desc}{hint_str}{caveat_str}"

        if rel == "declared_fk":
            fk_lines.append(entry)
        else:
            logical_lines.append(entry)

    sections: list[str] = []
    if fk_lines:
        sections.append("#### Declared Physical Foreign Keys:")
        sections.extend(fk_lines)
    if logical_lines:
        if fk_lines:
            sections.append("")
        sections.append("#### Logical Domain Relationships:")
        sections.extend(logical_lines)

    return "\n".join(sections).strip()


def format_metrics_for_prompt(metastore: dict[str, Any]) -> str:
    """Format all canonical calculated metrics with explicit formula types, filters, and reference SQL."""
    lines: list[str] = []
    metrics = metastore.get("calculated_metrics", {})
    if not metrics:
        return "None"

    for mname, mdata in sorted(metrics.items()):
        desc = mdata.get("description", "")
        ftype = mdata.get("formula_type", "aggregate").upper()
        expr = mdata.get("sql_expression", "")
        req_tables = ", ".join(f"`{t}`" for t in mdata.get("required_tables", [])) or "None"
        req_joins = mdata.get("required_joins", [])
        filters = mdata.get("filters", [])
        date_filter = mdata.get("date_filter", "")
        depends = mdata.get("depends_on", [])
        alt = mdata.get("alternative", "")
        canon = mdata.get("canonical_sql", "")

        lines.append(f"#### Metric: `{mname}` [{ftype}]")
        lines.append(f"- **Description**: {desc}")
        lines.append(f"- **SQL Expression**: `{expr}`")
        lines.append(f"- **Required Tables**: {req_tables}")
        if depends:
            lines.append(f"- **Depends On**: {', '.join(f'`{d}`' for d in depends)}")
        if req_joins:
            lines.append(f"- **Required Joins**: {'; '.join(req_joins)}")
        if filters:
            lines.append(f"- **Mandatory Filters**: {'; '.join(filters)}")
        if date_filter:
            lines.append(f"- **Date Filter Expression**: `{date_filter}`")
        if alt:
            lines.append(f"- **Alternative Formulation**: {alt}")
        if canon:
            lines.append("- **Canonical Reference SQL**:")
            lines.append(f"```sql\n{canon.strip()}\n```")
        lines.append("")

    return "\n".join(lines).strip()


def format_business_rules_for_prompt(metastore: dict[str, Any]) -> str:
    """Format authoritative domain accounting rules and query conventions."""
    rules = metastore.get("business_rules", [])
    if not rules:
        return "None"
    lines: list[str] = []
    for idx, rule in enumerate(rules, 1):
        lines.append(f"{idx}. {rule}")
    return "\n".join(lines).strip()


def format_data_caveats_for_prompt(metastore: dict[str, Any]) -> str:
    """Format database quirks (VARCHAR decimals, reserved words, collation, missing PKs)."""
    caveats = metastore.get("data_caveats", [])
    if not caveats:
        return "None"
    lines: list[str] = []
    for c in caveats:
        tbl = f"`{c['table']}`" if c.get("table") else "GLOBAL"
        col = f".`{c['column']}`" if c.get("column") else ""
        issue = c.get("issue", "GENERAL")
        sev = str(c.get("severity", "medium")).upper()
        guidance = c.get("guidance", "")
        lines.append(f"- **{tbl}{col} [{issue} - {sev}]**: {guidance}")
    return "\n".join(lines).strip()


def generate_correction_hint(error_code: str | None, error_message: str | None) -> str:
    """Generate targeted guidance based on error code and error message."""
    msg = (error_message or "").lower()
    code = (error_code or "").lower()
    if code == "unknown_table" or ("table" in msg and "not in" in msg):
        return "The requested table does not exist in the tenant metastore. Review the SEMANTIC METASTORE SCHEMA and replace it with an allowed table."
    if code == "unknown_column" or "unknown column" in msg:
        return "Column does not exist on the referenced table. Verify column spellings in the schema. Back-quote reserved words: accounts.`group`, expensive.`date`, voucher.`Date`."
    if code in ("forbidden_statement", "forbidden_operation"):
        return "The reporting engine is strictly READ-ONLY. Emitting INSERT, UPDATE, DELETE, DROP, or ALTER is strictly forbidden. If the user asked to modify data, set sql_query to null and explain in error."
    if code == "restricted_table":
        return "Access to security or credential tables is strictly blocked. Set sql_query to null and populate error."
    if code == "multiple_statements":
        return "Multiple SQL statements (separated by semicolons) are forbidden. Emit exactly ONE single SELECT or CTE query."
    if "1055" in msg or "group by" in msg:
        return "MySQL ONLY_FULL_GROUP_BY is active. All non-aggregated columns in SELECT must appear in the GROUP BY clause."
    if "cast" in msg or "decimal" in msg or "truncate" in msg:
        return "Wrap VARCHAR columns storing numbers in CAST(col AS DECIMAL(15,2)) before applying arithmetic or comparisons."
    if "parse_error" in code or "syntax" in msg:
        return "Review SQL syntax. Check quotation marks, aliases, table prefixes, and closing parentheses."
    return "Fix the specific SQL error identified above. Ground all identifiers strictly in the provided metastore schema."


def format_error_context_for_prompt(error_context: dict[str, Any]) -> str:
    """Format self-correction error context into a structured instruction block for retry prompts."""
    attempt = error_context.get("attempt") or 1
    max_attempts = error_context.get("max_attempts") or 2
    prev_sql = error_context.get("previous_sql")
    err_code = error_context.get("error_code") or error_context.get("error_category") or "EXECUTION_ERROR"
    err_msg = error_context.get("error_message") or "Unknown error encountered."
    hint = error_context.get("hint") or generate_correction_hint(err_code, err_msg)

    sql_block = f"```sql\n{prev_sql.strip()}\n```" if prev_sql else "None"

    lines = [
        f"### SELF-CORRECTION REQUIRED (Retry Attempt {attempt} of {max_attempts}):",
        "Your previous output failed AST validation or database execution. Correct the query following the diagnostic feedback below:",
        "",
        "**Previous Failed SQL Query**:",
        sql_block,
        "",
        f"- **Error Classification**: `{err_code}`",
        f"- **Error Details**: {err_msg}",
        f"- **Actionable Correction Hint**: {hint}",
        "",
        "**CRITICAL RETRY INSTRUCTIONS**:",
        "1. Address the root cause identified in the error details and hint above.",
        "2. Ground all tables, columns, and joins strictly in the SEMANTIC METASTORE SCHEMA below.",
        "3. Preserve the ReportPlanSchema invariant: exactly one of `sql_query` or `error` must be non-null.",
    ]
    return "\n".join(lines).strip()


def build_system_prompt(metastore: dict[str, Any]) -> str:
    """Construct the immutable, security-hardened system prompt."""
    dialect = metastore.get("sql_dialect", "mysql").upper()
    tenant = metastore.get("tenant_name", "Tenant ERP")
    schema_text = format_schema_for_prompt(metastore)
    joins_text = format_joins_for_prompt(metastore)
    metrics_text = format_metrics_for_prompt(metastore)
    rules_text = format_business_rules_for_prompt(metastore)
    caveats_text = format_data_caveats_for_prompt(metastore)
    restricted = metastore.get("security", {}).get("restricted_tables", [])
    max_limit = metastore.get("security", {}).get("max_row_limit", 1000)

    prompt = f"""You are the strictly READ-ONLY Text-to-SQL Orchestrator for {tenant}.
Your duty is to generate precise, safe, and performant {dialect} queries that answer business questions, or politely refuse requests that cannot or must not be fulfilled.

### SECURITY & EXECUTION RULES (CRITICAL):
1. **STRICT READ-ONLY ENFORCEMENT**:
   - You MUST ONLY emit a single SELECT, UNION, or CTE (`WITH ... SELECT`) statement.
   - Absolutely NO DDL (CREATE, DROP, ALTER, TRUNCATE), DML (INSERT, UPDATE, DELETE), DCL (GRANT, REVOKE), or session commands.
   - If the user asks to modify, delete, update, insert, or drop any data or schema, you MUST REFUSE by setting `sql_query` to null and explaining the refusal in `error`.

2. **RESTRICTED & EXCLUDED TABLES**:
   - Access to credentials or security tables {restricted} is strictly forbidden.
   - If asked about credentials, tokens, passwords, or restricted tables, set `sql_query` to null and populate `error`.

3. **SCHEMA GROUNDING & ZERO HALLUCINATION**:
   - You MUST ONLY query the tables and columns explicitly listed in the Semantic Metastore below.
   - NEVER invent or assume the existence of any column or table not listed.
   - If a requested metric or attribute is not supported by the schema, set `sql_query` to null and explain what is missing in `error`.

4. **CANONICAL CALCULATED METRICS & RECONCILIATION**:
   - When the user asks for financial, P&L, revenue, inventory, or balance metrics, you MUST strictly follow the definitions in CALCULATED METRICS & CANONICAL FORMULAS below:
     * `total_sales_revenue`: SUM of `voucher.TotalAmount` WHERE `TransactionType = 'Sales'` and status NOT IN ('Cancelled', 'canceled').
     * `cost_of_goods_sold`: SUM of `voucherdetails.quantity * COALESCE(products.purchase_price, 0)` joined via `LEFT JOIN products ON products.goods_name = voucherdetails.product` and `voucherdetails.transaction_type = 'Sales'`.
     * `total_expenses`: SUM of `expensive.amount` WHERE status NOT IN ('Cancelled', 'canceled').
     * `gross_profit`: `total_sales_revenue - cost_of_goods_sold`.
     * `net_profit`: `gross_profit - total_expenses`.
     * `party_unpaid_balance`: Uses `COALESCE(CAST(accounts.unpaid_amount AS DECIMAL(15,2)), 0)` where balance > 0.
     * `total_purchases`: SUM of `voucher.TotalAmount` WHERE `TransactionType = 'Purchase'` and status NOT cancelled.
     * `batch_stock_on_hand`: Live on-hand stock and valuation from `batches` joined with `products`.

5. **DATA CAVEATS & TYPE CASTS**:
   - Columns stored as VARCHAR holding numbers (e.g. `accounts.unpaid_amount`, `batches.quantity`, `batches.purchase_price`, `products.mrp`) MUST be cast using `CAST(col AS DECIMAL(15,2))` before arithmetic, aggregation, or numeric filtering.
   - Quote reserved SQL keywords: `accounts.`group``, `expensive.`date``, `voucher.`Date``.
   - `batches.exp_date` is VARCHAR: use `STR_TO_DATE(exp_date, '%Y-%m-%d')`.

6. **ROW LIMIT**:
   - Every query should return a reasonable number of rows. An upper limit of {max_limit} rows is enforced by the security gate.

7. **RESPONSE FORMAT & INVARIANT (MANDATORY)**:
   - Your response MUST be valid JSON adhering strictly to `ReportPlanSchema`.
   - Fields required in JSON object:
     * `"thought_process"`: string (1-4000 chars) explaining tables, joins, metrics, and filters selected.
     * `"tables_used"`: list of physical tables referenced (e.g. `["voucher", "voucherdetails"]`), or `[]` if refused.
     * `"metrics_applied"`: list of canonical calculated metrics used verbatim (e.g. `["gross_profit"]`), or `[]`.
     * `"sql_query"`: string or null. Single read-only MySQL SELECT statement.
     * `"suggested_visualization"`: one of `"table"`, `"bar"`, `"line"`, `"pie"`, `"kpi_card"`.
     * `"error"`: string or null. Populated only when request is refused or cannot be answered.
   - **MUTUAL EXCLUSIVITY INVARIANT**:
     * If query is generated: `"sql_query"` is a non-empty string AND `"error"` is `null`.
     * If query is refused: `"sql_query"` is `null` AND `"error"` is a non-empty explanation string.
     * NEVER emit both `"sql_query"` and `"error"` as null.
     * NEVER emit both `"sql_query"` and `"error"` as populated.

---

### SEMANTIC METASTORE SCHEMA:
{schema_text}

---

### ALLOWED JOINS & RELATIONSHIPS:
{joins_text}

---

### CALCULATED METRICS & CANONICAL FORMULAS:
{metrics_text}

---

### BUSINESS RULES & ACCOUNTING CONVENTIONS:
{rules_text}

---

### DATA CAVEATS & TYPE CASTS:
{caveats_text}
"""
    return prompt.strip()


def build_retry_user_prompt(
    user_question: str,
    error_context: dict[str, Any],
    current_date: str | None = None,
) -> str:
    """Construct an enriched user prompt injecting error feedback for self-correction."""
    resolved_date = current_date or datetime.date.today().isoformat()
    error_section = format_error_context_for_prompt(error_context)
    return (
        f"Today's date is: {resolved_date}\n\n"
        f"User Question:\n{user_question.strip()}\n\n"
        f"{error_section}"
    )


def build_inference_prompt(
    user_question: str,
    metastore: dict[str, Any],
    modules: Iterable[str] | None = None,
    current_date: str | None = None,
    error_context: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Prune metastore to relevant scopes, then return (system_prompt, user_prompt)."""
    resolved_date = current_date or datetime.date.today().isoformat()
    pruned = prune_metastore(metastore, modules=modules)
    system_prompt = build_system_prompt(pruned)
    if error_context:
        user_prompt = build_retry_user_prompt(
            user_question=user_question,
            error_context=error_context,
            current_date=resolved_date,
        )
    else:
        user_prompt = f"Today's date is: {resolved_date}\n\nUser Question:\n{user_question.strip()}"
    return system_prompt, user_prompt
