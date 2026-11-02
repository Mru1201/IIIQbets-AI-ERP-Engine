"""Gretel-Style Synthetic Multi-Table ERP Query Generator.

Produces 600 complex ERP SQL reporting queries grounded directly in the
shree_shashwatraj_agro ledger schema, covering:
- Multi-table joins (2-4 tables)
- Date intervals and ranges (BETWEEN, MONTH, YEAR)
- Grouping, rollups, and aggregations (GROUP BY ... HAVING ...)
- Window functions (ROW_NUMBER() OVER ...)
- Type casting and back-quoting of reserved words
- Canonical calculated metrics
"""

from __future__ import annotations

import json
import random
from typing import Any

QUERY_PATTERNS = [
    # Pattern 1: Multi-table join with product sales rollups
    {
        "prompt_tmpl": "Show total sales volume and revenue by product category in {city}",
        "tables": ["voucher", "voucherdetails", "products", "categories", "accounts"],
        "metrics": ["total_sales_revenue"],
        "sql_tmpl": "SELECT c.category_name, p.goods_name, SUM(vd.quantity) AS total_units, SUM(vd.total) AS total_revenue FROM voucher v JOIN accounts a ON a.id = v.AccountID JOIN voucherdetails vd ON vd.voucher_id = v.VoucherID JOIN products p ON p.goods_name = vd.product JOIN categories c ON p.category_id = c.id WHERE v.TransactionType = 'Sales' AND a.billing_city = '{city}' GROUP BY c.category_name, p.goods_name ORDER BY total_revenue DESC",
        "vis": "bar",
    },
    # Pattern 2: Window function for top selling product per party
    {
        "prompt_tmpl": "Rank top purchased products per customer by revenue contribution",
        "tables": ["voucher", "voucherdetails", "accounts"],
        "metrics": ["total_sales_revenue"],
        "sql_tmpl": "SELECT party_name, product, revenue, rank_num FROM (SELECT a.name AS party_name, vd.product, SUM(vd.total) AS revenue, ROW_NUMBER() OVER (PARTITION BY a.name ORDER BY SUM(vd.total) DESC) AS rank_num FROM voucher v JOIN accounts a ON a.id = v.AccountID JOIN voucherdetails vd ON vd.voucher_id = v.VoucherID WHERE v.TransactionType = 'Sales' GROUP BY a.name, vd.product) ranked WHERE rank_num <= 3",
        "vis": "table",
    },
    # Pattern 3: Financial profit and loss calculation with COGS
    {
        "prompt_tmpl": "Calculate gross margin and profit per sales transaction in {month_name} 2026",
        "tables": ["voucher", "voucherdetails", "products"],
        "metrics": ["gross_profit", "cost_of_goods_sold"],
        "sql_tmpl": "SELECT v.InvoiceNumber, v.Date, v.TotalAmount AS revenue, SUM(vd.quantity * COALESCE(p.purchase_price, 0)) AS cogs, (v.TotalAmount - SUM(vd.quantity * COALESCE(p.purchase_price, 0))) AS gross_profit FROM voucher v JOIN voucherdetails vd ON vd.voucher_id = v.VoucherID LEFT JOIN products p ON p.goods_name = vd.product WHERE v.TransactionType = 'Sales' AND DATE(v.Date) BETWEEN '2026-{month_num}-01' AND '2026-{month_num}-28' GROUP BY v.InvoiceNumber, v.Date, v.TotalAmount",
        "vis": "line",
    },
    # Pattern 4: Staff expenses breakdown with having clause
    {
        "prompt_tmpl": "Find staff members claiming more than {min_amt} in {category} expenses",
        "tables": ["expensive"],
        "metrics": ["total_expenses"],
        "sql_tmpl": "SELECT staff_name, category, SUM(amount) AS total_expense FROM expensive WHERE category = '{category}' GROUP BY staff_name, category HAVING SUM(amount) > {min_amt} ORDER BY total_expense DESC",
        "vis": "bar",
    },
    # Pattern 5: Party aging and unpaid balance with casting
    {
        "prompt_tmpl": "List party accounts with unpaid amount exceeding {pct}% of credit limit",
        "tables": ["accounts"],
        "metrics": ["party_unpaid_balance"],
        "sql_tmpl": "SELECT id, name, `group`, CAST(unpaid_amount AS DECIMAL(15,2)) AS unpaid, credit_limit, ROUND(CAST(unpaid_amount AS DECIMAL(15,2)) * 100.0 / NULLIF(credit_limit, 0), 2) AS utilization_pct FROM accounts WHERE role = 'retailer' AND credit_limit > 0 AND (CAST(unpaid_amount AS DECIMAL(15,2)) * 100.0 / credit_limit) > {pct} ORDER BY utilization_pct DESC",
        "vis": "table",
    },
    # Pattern 6: Batch inventory valuation and shelf life
    {
        "prompt_tmpl": "Stock valuation and batch expiry for {product_name}",
        "tables": ["batches", "products"],
        "metrics": ["batch_stock_on_hand"],
        "sql_tmpl": "SELECT p.goods_name, b.batch_number, b.quantity, b.purchase_price, (b.quantity * b.purchase_price) AS valuation, b.exp_date FROM batches b JOIN products p ON b.product_id = p.id WHERE p.goods_name LIKE '%{product_name}%' AND b.quantity > 0 ORDER BY b.exp_date ASC",
        "vis": "table",
    },
    # Pattern 7: Monthly revenue and sales tax rollup
    {
        "prompt_tmpl": "Monthly breakdown of sales revenue, CGST, and SGST collected",
        "tables": ["voucher", "voucherdetails"],
        "metrics": ["total_sales_revenue"],
        "sql_tmpl": "SELECT strftime('%Y-%m', v.Date) AS sales_month, SUM(v.TotalAmount) AS revenue, SUM(vd.cgst) AS total_cgst, SUM(vd.sgst) AS total_sgst FROM voucher v JOIN voucherdetails vd ON vd.voucher_id = v.VoucherID WHERE v.TransactionType = 'Sales' GROUP BY sales_month ORDER BY sales_month DESC",
        "vis": "bar",
    },
    # Pattern 8: Party ledger reconciliation
    {
        "prompt_tmpl": "Reconcile vouchers and outstanding balance for party account ID {party_id}",
        "tables": ["voucher", "accounts"],
        "metrics": ["party_unpaid_balance", "total_sales_revenue"],
        "sql_tmpl": "SELECT a.name, a.balance_amount, v.VoucherID, v.TransactionType, v.Date, v.TotalAmount FROM accounts a JOIN voucher v ON v.AccountID = a.id WHERE a.id = {party_id} ORDER BY v.Date DESC",
        "vis": "table",
    },
]


def generate_erp_synthetic_samples(
    count: int = 600,
    seed: int = 43,
) -> list[dict[str, Any]]:
    """Generate 600 Gretel-style multi-table synthetic ERP reporting samples."""
    rng = random.Random(seed)
    samples: list[dict[str, Any]] = []

    cities = ["Indore", "Bhopal", "Dewas", "Ujjain", "Khandwa", "Jabalpur", "Gwalior"]
    months = [("January", "01"), ("February", "02"), ("March", "03"), ("October", "10"), ("November", "11"), ("December", "12")]
    exp_cats = ["Petrol", "Desel", "Traveling", "Office", "Food"]
    products = ["D-K-60", "SARVOTTAM", "CHELATED", "ZINC", "POTASH", "BORON", "NPK"]

    system_prompt = (
        "You are an expert AI ERP Ledger Reporting Assistant.\n"
        "Generate structured JSON complying strictly with ReportPlanSchema.\n"
        "Tenant ID: shree_shashwatraj_agro\n"
        "Database Dialect: MySQL\n"
        "Metastore Schema: Multi-table relational ledger schema with accounts, voucher, voucherdetails, products, batches, expensive."
    )

    while len(samples) < count:
        pattern = rng.choice(QUERY_PATTERNS)
        city = rng.choice(cities)
        m_name, m_num = rng.choice(months)
        cat = rng.choice(exp_cats)
        prod = rng.choice(products)
        party_id = rng.randint(1, 50)
        min_amt = rng.choice([2000, 5000, 10000, 15000])
        pct = rng.choice([50, 75, 80, 90])

        q = pattern["prompt_tmpl"].format(
            city=city,
            month_name=m_name,
            month_num=m_num,
            category=cat,
            product_name=prod,
            party_id=party_id,
            min_amt=min_amt,
            pct=pct,
        )

        sql = pattern["sql_tmpl"].format(
            city=city,
            month_name=m_name,
            month_num=m_num,
            category=cat,
            product_name=prod,
            party_id=party_id,
            min_amt=min_amt,
            pct=pct,
        )

        plan = {
            "thought_process": f"Identified multi-table relational join over {pattern['tables']}. Applied metrics {pattern['metrics']} with filters.",
            "tables_used": pattern["tables"],
            "metrics_applied": pattern["metrics"],
            "sql_query": sql,
            "suggested_visualization": pattern["vis"],
            "error": None,
        }

        samples.append({
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Today's date is: 2026-11-02\n\nUser Question:\n{q}"},
                {"role": "assistant", "content": json.dumps(plan)},
            ]
        })

    return samples
