"""Dynamic UI Schema Derivation Engine.

Analyzes raw query results, column names, and data types to infer optimal
visualization views, formatted column definitions, and executive KPI cards.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import re
from typing import Any

from app.schemas.api_models import (
    ColumnDataType,
    ColumnDef,
    KPICard,
    UISchema,
    VisualizationType,
)

CURRENCY_KEYWORDS: frozenset[str] = frozenset({
    "amount",
    "price",
    "revenue",
    "cogs",
    "profit",
    "loss",
    "cost",
    "balance",
    "total",
    "sales",
    "purchase",
    "spend",
    "unpaid",
    "debit",
    "credit",
    "fee",
    "tax",
    "cgst",
    "sgst",
    "igst",
    "discount",
    "mrp",
    "gross_profit",
    "net_profit",
    "unpaid_amount",
    "totalamount",
    "purchase_price",
    "party_unpaid_balance",
})

DATE_KEYWORDS: frozenset[str] = frozenset({
    "date",
    "created_at",
    "updated_at",
    "timestamp",
    "time",
    "month",
    "year",
    "day",
    "period",
    "exp_date",
    "voucher_date",
})

NUMBER_KEYWORDS: frozenset[str] = frozenset({
    "quantity",
    "qty",
    "count",
    "num",
    "number",
    "id",
    "code",
    "rate",
    "percent",
    "percentage",
    "margin",
    "row_count",
    "invoice_count",
    "units",
    "units_sold",
})

DATE_REGEX = re.compile(r"^\d{4}-\d{2}(-\d{2})?(?:[ T]\d{2}:\d{2}:\d{2})?")


def format_column_label(key: str) -> str:
    """Format snake_case or camelCase key into clean Title Case label."""
    words = re.sub(r"([a-z])([A-Z])", r"\1 \2", key).replace("_", " ").split()
    acronyms = {
        "id": "ID",
        "cogs": "COGS",
        "gst": "GST",
        "cgst": "CGST",
        "sgst": "SGST",
        "igst": "IGST",
        "sku": "SKU",
        "pk": "PK",
        "kpi": "KPI",
    }
    formatted = [acronyms.get(w.lower(), w.capitalize()) for w in words]
    return " ".join(formatted) if formatted else key


def _is_number_column(lowered: str) -> bool:
    tokens = set(re.split(r"[^a-z0-9]+", lowered))
    if any(k in tokens for k in NUMBER_KEYWORDS):
        return True
    if lowered.endswith(("_count", "_qty", "_units", "_units_sold")):
        return True
    return False


def infer_column_type(key: str, sample_values: list[Any]) -> ColumnDataType:
    """Infer semantic column data type using key naming and value inspection."""
    lowered = key.lower()
    non_null = [v for v in sample_values if v is not None]

    if non_null:
        # Check for boolean
        if all(isinstance(v, bool) for v in non_null):
            return "boolean"

        # Check for date / datetime
        if all(
            isinstance(v, (date, datetime))
            or (isinstance(v, str) and DATE_REGEX.match(v.strip()))
            for v in non_null
        ):
            return "date"

        # Check for numeric (number vs currency)
        if all(isinstance(v, (int, float, Decimal)) for v in non_null):
            if _is_number_column(lowered):
                return "number"
            if any(k in lowered for k in CURRENCY_KEYWORDS):
                return "currency"
            return "number"

    # Fallback heuristics based on column key naming
    if _is_number_column(lowered):
        return "number"
    if any(k in lowered for k in CURRENCY_KEYWORDS):
        return "currency"
    if any(k in lowered for k in DATE_KEYWORDS):
        return "date"
    if lowered.startswith(("is_", "has_")):
        return "boolean"

    return "string"


def derive_column_defs(columns: list[str], data: list[dict[str, Any]]) -> list[ColumnDef]:
    """Build list of ColumnDef objects with inferred types and alignments."""
    column_defs: list[ColumnDef] = []
    sample_rows = data[:50]

    for col in columns:
        sample_vals = [row.get(col) for row in sample_rows]
        col_type = infer_column_type(col, sample_vals)
        align = "right" if col_type in ("currency", "number") else ("center" if col_type == "date" else "left")
        column_defs.append(
            ColumnDef(
                key=col,
                label=format_column_label(col),
                type=col_type,
                align=align,
                sortable=True,
            )
        )
    return column_defs


def derive_suggested_visualization(
    columns: list[str],
    column_defs: list[ColumnDef],
    data: list[dict[str, Any]],
    llm_suggestion: str | None = None,
) -> VisualizationType:
    """Determine optimal primary visualization for the dataset."""
    row_count = len(data)
    if row_count == 0:
        return "table"

    # Map LLM suggestion tokens to valid VisualizationType
    llm_map: dict[str, VisualizationType] = {
        "kpi_card": "kpi_grid",
        "kpi_grid": "kpi_grid",
        "bar": "bar_chart",
        "bar_chart": "bar_chart",
        "line": "line_chart",
        "line_chart": "line_chart",
        "pie": "donut_chart",
        "donut_chart": "donut_chart",
        "table": "table",
    }
    normalized_llm: VisualizationType | None = (
        llm_map.get(str(llm_suggestion).lower()) if llm_suggestion is not None else None
    )

    # 1. Single summary row with numeric metrics -> kpi_grid
    if row_count == 1:
        numeric_cols = [c for c in column_defs if c.type in ("currency", "number")]
        if len(numeric_cols) >= 1:
            return "kpi_grid"

    # 2. Key-value metric table (e.g. [{"metric": "Gross Profit", "amount": 2468}])
    if 1 <= row_count <= 6 and set(columns) & {"metric", "label", "kpi"} and set(columns) & {"amount", "value"}:
        return "kpi_grid"

    # 3. If LLM requested table or dataset has >= 4 columns (wide table), prefer table
    if normalized_llm == "table" or len(columns) >= 4:
        return "table"

    # 4. Time series trend -> line_chart
    date_cols = [c for c in column_defs if c.type == "date"]
    numeric_cols = [c for c in column_defs if c.type in ("currency", "number")]
    if row_count >= 2 and len(date_cols) >= 1 and len(numeric_cols) >= 1:
        return "line_chart"

    # 5. Small categorical breakdown -> donut_chart (unless LLM explicitly requested bar_chart)
    string_cols = [c for c in column_defs if c.type == "string"]
    if 2 <= row_count <= 6 and len(string_cols) == 1 and len(numeric_cols) >= 1:
        if normalized_llm == "bar_chart":
            return "bar_chart"
        return "donut_chart"

    # 6. Categorical ranking / comparison -> bar_chart
    if 2 <= row_count <= 25 and len(string_cols) >= 1 and len(numeric_cols) >= 1:
        return "bar_chart"

    # 7. Fallback or wide table -> table
    return "table"


def format_kpi_value(val: Any, col_type: ColumnDataType) -> str | float:
    """Format KPI value for presentation."""
    if val is None:
        return "0.00"
    if isinstance(val, (int, float, Decimal)):
        numeric = round(float(val), 2)
        if col_type == "currency":
            return f"{numeric:,.2f} INR"
        return numeric
    return str(val)


def derive_kpi_cards(
    column_defs: list[ColumnDef],
    data: list[dict[str, Any]],
    metrics_applied: list[str] | None = None,
) -> list[KPICard]:
    """Derive up to 4 executive KPI summary cards from query data."""
    if not data:
        return []

    cards: list[KPICard] = []
    row_count = len(data)

    # Case A: Single Summary Row (e.g. total_sales_revenue: 10368.0)
    if row_count == 1:
        row = data[0]
        for col in column_defs:
            if col.type in ("currency", "number") and col.key in row:
                raw_val = row[col.key]
                val_float = float(raw_val) if isinstance(raw_val, (int, float, Decimal)) else 0.0
                trend = None
                low_key = col.key.lower()
                if "profit" in low_key:
                    trend = "positive" if val_float > 0 else ("negative" if val_float < 0 else "neutral")
                elif "loss" in low_key or "expense" in low_key:
                    trend = "negative" if val_float > 0 else "positive"
                elif "revenue" in low_key or "sales" in low_key:
                    trend = "positive" if val_float > 0 else "neutral"

                cards.append(
                    KPICard(
                        label=col.label,
                        value=format_kpi_value(raw_val, col.type),
                        trend=trend,
                        subtext=f"Metric: {col.label}",
                    )
                )
                if len(cards) >= 4:
                    break
        return cards

    # Case B: Key-Value Metric Rows (e.g. [{"metric": "Gross Profit", "amount": 2468.0}])
    if set(data[0].keys()) & {"metric", "label"} and set(data[0].keys()) & {"amount", "value"}:
        label_key = "metric" if "metric" in data[0] else "label"
        val_key = "amount" if "amount" in data[0] else "value"
        for row in data[:4]:
            raw_val = row.get(val_key, 0)
            val_float = float(raw_val) if isinstance(raw_val, (int, float, Decimal)) else 0.0
            trend = "positive" if val_float > 0 else ("negative" if val_float < 0 else "neutral")
            cards.append(
                KPICard(
                    label=str(row.get(label_key, "Metric")),
                    value=format_kpi_value(raw_val, "currency"),
                    trend=trend,
                )
            )
        return cards

    # Case C: Multi-row Tabular Data (e.g. List of Debtors)
    cards.append(
        KPICard(
            label="Total Records",
            value=row_count,
            trend="neutral",
            subtext="Rows returned",
        )
    )

    # Find primary currency column to aggregate
    currency_cols = [c for c in column_defs if c.type == "currency"]
    if currency_cols:
        primary = currency_cols[0]
        total_sum = sum(
            float(r[primary.key])
            for r in data
            if isinstance(r.get(primary.key), (int, float, Decimal))
        )
        cards.append(
            KPICard(
                label=f"Total {primary.label}",
                value=format_kpi_value(total_sum, "currency"),
                trend="neutral",
                subtext=f"Sum across all {row_count} records",
            )
        )
        if row_count > 1:
            avg_val = total_sum / row_count
            cards.append(
                KPICard(
                    label=f"Avg {primary.label}",
                    value=format_kpi_value(avg_val, "currency"),
                    trend="neutral",
                    subtext="Mean per record",
                )
            )

    return cards[:4]


def derive_ui_schema(
    columns: list[str],
    data: list[dict[str, Any]],
    metrics_applied: list[str] | None = None,
    llm_suggestion: str | None = None,
) -> UISchema:
    """Top-level facade to derive complete UISchema for frontend consumption."""
    column_defs = derive_column_defs(columns, data)
    vis = derive_suggested_visualization(columns, column_defs, data, llm_suggestion)
    kpis = derive_kpi_cards(column_defs, data, metrics_applied)
    return UISchema(
        suggested_visualization=vis,
        columns=column_defs,
        kpis=kpis,
        filters=[],
    )
