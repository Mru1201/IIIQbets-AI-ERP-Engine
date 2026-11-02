"""BIRD-SQL Dataset Converter with Evidence-to-Calculated-Metrics Mapping.

Converts BIRD-SQL schema and evidence into:
- Metastore JSON schemas.
- Domain calculated metrics (e.g. "evidence": "margin = price - cost" -> calculated_metrics["margin"]).
- Valid ChatML format with ReportPlanSchema assistant responses.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Optional

import sqlglot
from sqlglot import exp

EVIDENCE_TEMPLATES = [
    ("gross_profit", "gross profit = total sales amount - cost of goods sold", "TotalAmount - (quantity * purchase_price)", ["voucher", "voucherdetails", "products"]),
    ("net_receivable", "net receivable = unpaid amount - credit limit allowance", "unpaid_amount - credit_limit", ["accounts"]),
    ("discounted_price", "discounted price = unit price - (unit price * discount / 100)", "price * (1 - discount / 100.0)", ["voucherdetails"]),
    ("tax_inclusive_total", "tax inclusive total = base amount + cgst + sgst + igst", "total + cgst + sgst + igst", ["voucherdetails"]),
    ("stock_valuation", "stock valuation = remaining stock quantity * unit purchase price", "quantity * purchase_price", ["batches"]),
    ("profit_margin_pct", "profit margin = (selling price - cost price) / selling price * 100", "(selling_price - cost_price) * 100.0 / NULLIF(selling_price, 0)", ["batches"]),
    ("effective_sales_tax", "effective sales tax = (cgst + sgst + igst) / total amount * 100", "(cgst + sgst + igst) * 100.0 / NULLIF(total, 0)", ["voucherdetails"]),
    ("daily_run_rate", "daily expense run rate = total operating expense / days in month", "SUM(amount) / 30.0", ["expensive"]),
    ("net_turnover", "net turnover = gross sales amount - discount amount", "TotalAmount - discount_charges_amount", ["voucher"]),
    ("avg_order_value", "average order value = total sales / number of sales vouchers", "SUM(TotalAmount) / COUNT(VoucherID)", ["voucher"]),
]

BASE_SYSTEM_PROMPT_TEMPLATE = """You are an expert AI ERP Ledger Reporting Assistant.
Generate structured JSON complying strictly with ReportPlanSchema.
Tenant ID: {tenant_id}
Database Dialect: MySQL
Metastore Schema:
{metastore_json}
Calculated Metrics:
{metrics_json}
"""


def create_bird_metastore(tables: list[str], metric_name: str, metric_expr: str, required_tables: list[str]) -> dict[str, Any]:
    """Build mini metastore embedding the converted evidence as a calculated metric."""
    metastore = {
        "tenant_id": "bird_erp_converted",
        "sql_dialect": "mysql",
        "tables": {
            t: {
                "description": f"BIRD-SQL converted table {t}",
                "module_scope": ["accounting", "sales_purchase"],
                "columns": {
                    "id": {"data_type": "int", "description": "Primary key"},
                    "name": {"data_type": "varchar(100)", "description": "Name field"},
                    "amount": {"data_type": "decimal(12,2)", "description": "Monetary value"},
                },
            }
            for t in tables
        },
        "calculated_metrics": {
            metric_name: {
                "description": f"Metric derived from BIRD-SQL evidence: {metric_name}",
                "formula_type": "derived",
                "sql_expression": metric_expr,
                "required_tables": required_tables,
            }
        },
    }
    return metastore


def generate_bird_samples(
    count: int = 2100,
    seed: int = 42,
    source_file: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Generate BIRD-SQL samples mapping evidence strings into calculated_metrics."""
    rng = random.Random(seed)
    samples: list[dict[str, Any]] = []

    # If external BIRD-SQL json file is provided, load and convert
    if source_file and Path(source_file).exists():
        with open(source_file, "r", encoding="utf-8") as f:
            raw_bird = json.load(f)
        for item in raw_bird[:count]:
            evidence = item.get("evidence", "Total calculation")
            metric_slug = "bird_evidence_metric"
            sql = item.get("SQL", "SELECT 1 FROM voucher")
            user_q = item.get("question", "BIRD ERP reporting query")

            try:
                parsed_ext = sqlglot.parse_one(sql, read="mysql")
                ext_tables = sorted({t.name.lower() for t in parsed_ext.find_all(exp.Table) if t.name}) or ["voucher"]
            except Exception:
                ext_tables = ["voucher"]

            plan = {
                "thought_process": f"Mapped evidence '{evidence}' to calculated metric '{metric_slug}'.",
                "tables_used": ext_tables,
                "metrics_applied": [metric_slug],
                "sql_query": sql,
                "suggested_visualization": "table",
                "error": None,
            }
            samples.append({
                "messages": [
                    {"role": "system", "content": f"BIRD-SQL converted assistant. Evidence: {evidence}"},
                    {"role": "user", "content": f"Today's date is: 2026-11-02\n\nUser Question:\n{user_q}"},
                    {"role": "assistant", "content": json.dumps(plan)},
                ]
            })

    # Procedural generation for the remaining / default count
    categories = ["electronics", "fertilizers", "seeds", "chemicals", "services", "machinery"]
    cities = ["Indore", "Bhopal", "Dewas", "Ujjain", "Khandwa", "Jabalpur"]

    while len(samples) < count:
        idx = len(samples) + 1
        metric_name, evidence_str, formula, req_tables = rng.choice(EVIDENCE_TEMPLATES)
        city = rng.choice(cities)
        cat = rng.choice(categories)

        user_questions = [
            f"Calculate the {metric_name.replace('_', ' ')} based on recent invoices in {city}.",
            f"Show our {metric_name.replace('_', ' ')} for {cat} products.",
            f"What is the average {metric_name.replace('_', ' ')} across active party accounts?",
            f"Provide total {metric_name.replace('_', ' ')} grouped by invoice number in {city}.",
            f"List top 5 transactions ranking by {metric_name.replace('_', ' ')}.",
        ]
        q = rng.choice(user_questions)

        if metric_name == "gross_profit":
            sql = f"SELECT v.InvoiceNumber, v.TotalAmount, SUM(vd.quantity * COALESCE(p.purchase_price, 0)) AS cogs, (v.TotalAmount - SUM(vd.quantity * COALESCE(p.purchase_price, 0))) AS gross_profit FROM voucher v JOIN voucherdetails vd ON vd.voucher_id = v.VoucherID LEFT JOIN products p ON p.goods_name = vd.product WHERE v.TransactionType = 'Sales' GROUP BY v.InvoiceNumber, v.TotalAmount LIMIT {rng.randint(5, 50)}"
            vis = "bar"
        elif metric_name == "net_receivable":
            sql = f"SELECT id, name, unpaid_amount, credit_limit, (unpaid_amount - credit_limit) AS net_receivable FROM accounts WHERE role = 'retailer' AND unpaid_amount > credit_limit ORDER BY net_receivable DESC LIMIT 10"
            vis = "table"
        elif metric_name == "discounted_price":
            sql = f"SELECT product, price, discount, (price * (1 - discount / 100.0)) AS discounted_price FROM voucherdetails WHERE discount > 0 LIMIT 20"
            vis = "table"
        elif metric_name == "stock_valuation":
            sql = f"SELECT batch_number, quantity, purchase_price, (quantity * purchase_price) AS valuation FROM batches WHERE quantity > 0 ORDER BY valuation DESC LIMIT 10"
            vis = "kpi"
        elif metric_name == "profit_margin_pct":
            sql = f"SELECT batch_number, selling_price, cost_price, ROUND((selling_price - cost_price) * 100.0 / NULLIF(selling_price, 0), 2) AS profit_margin_pct FROM batches WHERE selling_price > cost_price LIMIT 15"
            vis = "bar"
        elif metric_name == "tax_inclusive_total":
            sql = "SELECT id, product, total, cgst, sgst, igst, (total + cgst + sgst + igst) AS tax_inclusive_total FROM voucherdetails WHERE total > 0 LIMIT 15"
            vis = "table"
        elif metric_name == "effective_sales_tax":
            sql = "SELECT id, product, total, cgst, sgst, igst, ROUND((cgst + sgst + igst) * 100.0 / NULLIF(total, 0), 2) AS effective_sales_tax_pct FROM voucherdetails WHERE (cgst + sgst + igst) > 0 LIMIT 15"
            vis = "bar"
        elif metric_name == "daily_run_rate":
            sql = "SELECT category, SUM(amount) AS total_expense, ROUND(SUM(amount) / 30.0, 2) AS daily_run_rate FROM expensive GROUP BY category LIMIT 10"
            vis = "bar"
        elif metric_name == "net_turnover":
            sql = "SELECT VoucherID, InvoiceNumber, TotalAmount, discount_charges_amount, (TotalAmount - discount_charges_amount) AS net_turnover FROM voucher WHERE TransactionType = 'Sales' LIMIT 15"
            vis = "line"
        elif metric_name == "avg_order_value":
            sql = "SELECT TransactionType, COUNT(VoucherID) AS voucher_count, SUM(TotalAmount) AS total_sales, ROUND(SUM(TotalAmount) / NULLIF(COUNT(VoucherID), 0), 2) AS avg_order_value FROM voucher WHERE TransactionType = 'Sales' GROUP BY TransactionType"
            vis = "kpi"
        else:
            raise ValueError(f"Unsupported evidence metric: {metric_name}")

        metastore = create_bird_metastore(req_tables, metric_name, formula, req_tables)

        plan = {
            "thought_process": f"Identified business evidence: '{evidence_str}'. Grounded metric '{metric_name}' using formula '{formula}' over tables {req_tables}.",
            "tables_used": req_tables,
            "metrics_applied": [metric_name],
            "sql_query": sql,
            "suggested_visualization": vis,
            "error": None,
        }

        system_prompt = BASE_SYSTEM_PROMPT_TEMPLATE.format(
            tenant_id="shree_shashwatraj_agro",
            metastore_json=json.dumps(metastore["tables"]),
            metrics_json=json.dumps(metastore["calculated_metrics"]),
        )

        samples.append({
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Today's date is: 2026-11-02\n\nUser Question:\n{q}"},
                {"role": "assistant", "content": json.dumps(plan)},
            ]
        })

    return samples
