"""Tenant semantic overlay for ``shree_shashwatraj_agro``.

Curated business knowledge that cannot be introspected from DDL alone: module
scopes, logical (unenforced) joins, data caveats and the calculated metrics
codified *verbatim* from ``ledger-backend`` production SQL. The introspector
merges this overlay with the live/dump schema to produce ``metastore_schema.json``.

Source references point at the ground-truth backend (read-only, never modified).
"""

from __future__ import annotations

from typing import Any

# Shared production filter (profitLossRoutes.js L42/L64/L84). NOTE: voucher.status is
# ENUM('Pending','Partial','Paid'), so the 'Cancelled' branch never matches today; the
# filter is kept verbatim so AI numbers reconcile 1:1 with the existing P&L screen.
_NOT_CANCELLED_V = "(v.status NOT IN ('Cancelled', 'canceled') OR v.status IS NULL OR v.status = '')"
_NOT_CANCELLED_E = "(e.status NOT IN ('Cancelled', 'canceled') OR e.status IS NULL OR e.status = '')"
_DATE_V = "DATE(v.Date) BETWEEN '{from_date}' AND '{to_date}'"
_DATE_E = "DATE(e.`date`) BETWEEN '{from_date}' AND '{to_date}'"

_SALES_SQL = (
    "SELECT COALESCE(SUM(v.TotalAmount), 0) AS total_sales_revenue, COUNT(v.VoucherID) AS sales_invoice_count "
    f"FROM voucher v WHERE v.TransactionType = 'Sales' AND {_NOT_CANCELLED_V} AND {_DATE_V}"
)
_COGS_SQL = (
    "SELECT COALESCE(SUM(vd.quantity * COALESCE(p.purchase_price, 0)), 0) AS cost_of_goods_sold, "
    "COALESCE(SUM(vd.quantity), 0) AS total_units_sold "
    "FROM voucherdetails vd JOIN voucher v ON v.VoucherID = vd.voucher_id "
    "LEFT JOIN products p ON p.goods_name = vd.product "
    f"WHERE v.TransactionType = 'Sales' AND {_NOT_CANCELLED_V} AND vd.transaction_type = 'Sales' AND {_DATE_V}"
)
_EXPENSES_SQL = (
    "SELECT COALESCE(SUM(e.amount), 0) AS total_expenses, COUNT(*) AS expense_count "
    f"FROM expensive e WHERE {_NOT_CANCELLED_E} AND {_DATE_E}"
)
_PURCHASE_SQL = (
    "SELECT COALESCE(SUM(v.TotalAmount), 0) AS total_purchases, COUNT(v.VoucherID) AS purchase_invoice_count "
    f"FROM voucher v WHERE v.TransactionType = 'Purchase' AND {_NOT_CANCELLED_V} AND {_DATE_V}"
)
_PL_CTE = (
    f"WITH sales AS ({_SALES_SQL}), cogs AS ({_COGS_SQL}), expenses AS ({_EXPENSES_SQL}) "
    "SELECT s.total_sales_revenue, c.cost_of_goods_sold, "
    "s.total_sales_revenue - c.cost_of_goods_sold AS gross_profit, x.total_expenses, "
    "s.total_sales_revenue - c.cost_of_goods_sold - x.total_expenses AS net_profit "
    "FROM sales s CROSS JOIN cogs c CROSS JOIN expenses x"
)

TENANT_OVERLAY: dict[str, Any] = {
    "tenant_id": "shree_shashwatraj_agro",
    "tenant_name": "Shree Shashwatraj Agro Pvt Ltd",
    "sql_dialect": "mysql",
    "restricted_tables": ["gsp_api", "gsp_credentials"],
    "excluded_tables": {
        "chatbot_audit_log": "internal AI audit trail (not business data)",
    },
    # PII stripped in addition to the generic secret patterns (password/token/otp/secret).
    "excluded_columns": {
        "accounts": ["aadhaar_number", "bank_account_number", "upi_id"],
    },
    "modules": {
        "accounting": "Chart of accounts, parties (retailers/suppliers/staff), ledgers, vouchers, bank deposits.",
        "inventory": "Products, batches (live stock), categories, companies/brands, units, offers.",
        "sales_purchase": "Sales/Purchase/Receipt/Credit-Debit note vouchers and their line items, GST.",
        "orders": "Retailer app orders, order lines, carts, credit periods, field sales visits.",
        "finance_expenses": "Staff expenses, bank statement deposits, P&L inputs.",
    },
    "tables": {
        "accountgroup": {
            "description": "Chart-of-accounts groups (Sundry Debtors, Bank Accounts, ...).",
            "module_scope": ["accounting"],
        },
        "accounts": {
            "description": "Party master: retailers (customers), suppliers, staff, admin and ledger accounts. "
            "role identifies the party type.",
            "module_scope": ["accounting", "sales_purchase", "orders", "finance_expenses"],
            "columns": {
                "role": {"description": "Party type. Compare case-insensitively (collation is *_ci).",
                         "known_values": ["retailer", "supplier", "staff", "admin"]},
                "group": {"description": "Ledger group label (reserved word: always back-quote as `group`)."},
                "unpaid_amount": {"description": "Running outstanding receivable for the party (maintained by voucher routes).",
                                  "numeric_text": True},
                "opening_balance": {"description": "Opening ledger balance; direction in opening_balance_type (Debit/Credit).",
                                    "numeric_text": True},
                "staff_incentive": {"numeric_text": True},
                "credit_limit": {"description": "Credit limit in INR."},
                "balance_amount": {"description": "credit_limit - unpaid_amount (available credit)."},
                "is_dual_account": {"description": "1 when a supplier is also a retailer."},
                "name": {"description": "Party / person name."},
                "business_name": {"description": "Trade name of the party."},
                "status": {"description": "Account status; observed values '1' and 'Active' both mean active."},
            },
        },
        "batches": {
            "description": "Product batches holding LIVE stock. quantity is the running on-hand stock, mutated "
            "by every stock-in (Purchase/CreditNote/stock inward) and stock-out (Sales/DebitNote/stock transfer).",
            "module_scope": ["inventory"],
            "columns": {
                "quantity": {"description": "Current on-hand stock for the batch.", "numeric_text": True},
                "opening_stock": {"numeric_text": True},
                "stock_in": {"description": "Cumulative inward quantity.", "numeric_text": True},
                "stock_out": {"description": "Cumulative outward quantity.", "numeric_text": True},
                "cost_price": {"numeric_text": True},
                "selling_price": {"numeric_text": True},
                "purchase_price": {"numeric_text": True},
                "mrp": {"numeric_text": True},
                "min_sale_price": {"numeric_text": True},
                "batch_price": {"numeric_text": True},
                "exp_date": {"description": "Expiry date stored as VARCHAR (YYYY-MM-DD); use STR_TO_DATE(exp_date, '%Y-%m-%d')."},
                "mfg_date": {"description": "Manufacturing date stored as VARCHAR."},
            },
        },
        "cart_items": {"description": "Retailer app shopping cart lines.", "module_scope": ["orders"],
                       "columns": {"price": {"numeric_text": True}}},
        "categories": {"description": "Product categories with active discount.", "module_scope": ["inventory"]},
        "categories_discount_history": {"description": "Historical category discount windows.", "module_scope": ["inventory"]},
        "companies": {"description": "Manufacturer / brand companies (products.company_id).", "module_scope": ["inventory"]},
        "company_info": {"description": "Tenant's own legal entity details (single row).", "module_scope": ["accounting"]},
        "credit_periods": {"description": "Credit period (days) -> credit surcharge percentage.",
                           "module_scope": ["orders", "sales_purchase"],
                           "columns": {"credit_percentage": {"numeric_text": True}}},
        "direct_deposit": {"description": "Imported bank statement lines, reconciled to Receipt vouchers.",
                           "module_scope": ["finance_expenses", "accounting"],
                           "columns": {"status": {"known_values": ["pending", "approved"]},
                                       "party_id": {"description": "accounts.id stored as VARCHAR."}}},
        "expensive": {
            "description": "Staff operating expenses (table name is a legacy typo of 'expenses'). No primary key.",
            "module_scope": ["finance_expenses"],
            "columns": {
                "date": {"description": "Expense date (reserved word: back-quote as `date`)."},
                "amount": {"description": "Expense amount in INR."},
                "status": {"description": "Approval status (case varies).", "known_values": ["pending", "Pending", "approved"]},
                "payment_status": {"description": "Reimbursement state.", "known_values": ["unpaid", "paid"]},
            },
        },
        "offers": {"description": "Promotional offers / flash sales.", "module_scope": ["orders", "inventory"]},
        "orders": {
            "description": "Retailer app orders (header). Invoiced orders have invoice_status = 1.",
            "module_scope": ["orders"],
            "columns": {
                "net_payable": {"description": "Final payable amount for the order (used for retailer purchase totals)."},
                "order_status": {"description": "Lifecycle. Exclude 'Cancelled' from revenue."},
                "invoice_status": {"description": "1 = invoiced, 0 = not invoiced."},
                "order_mode": {"description": "KACHA (no GST invoice) / PAKKA (GST invoice)."},
            },
        },
        "order_items": {"description": "Order lines joined by order_number.", "module_scope": ["orders"]},
        "products": {
            "description": "Product master. purchase_price (INT) is the costing basis for COGS.",
            "module_scope": ["inventory", "sales_purchase", "orders"],
            "columns": {
                "goods_name": {"description": "Product name; production COGS joins voucherdetails.product = products.goods_name."},
                "purchase_price": {"description": "Unit cost used for COGS."},
                "price": {"description": "Selling price.", "numeric_text": True},
                "mrp": {"numeric_text": True},
                "net_price": {"numeric_text": True},
                "gst_rate": {"description": "GST % as text.", "numeric_text": True},
                "category_id": {"description": "categories.id stored as VARCHAR."},
                "company_id": {"description": "companies.id stored as VARCHAR."},
                "min_stock_alert": {"numeric_text": True},
                "max_stock_alert": {"numeric_text": True},
                "unit": {"numeric_text": False, "description": "Mostly units.id as text; occasionally a literal like 'pcs'."},
            },
        },
        "sales_visits": {"description": "Field sales staff visits to retailers.", "module_scope": ["orders", "sales_purchase"]},
        "saletypes": {"description": "Sale series / godown definitions.", "module_scope": ["sales_purchase"]},
        "units": {"description": "Units of measure.", "module_scope": ["inventory"]},
        "voucher": {
            "description": "Universal voucher header: Sales, Purchase, Receipt, Payment, CreditNote, DebitNote, Journal, "
            "Contra, stock transfer, stock inward, purchase voucher, Production, Consumption.",
            "module_scope": ["sales_purchase", "accounting"],
            "columns": {
                "TransactionType": {
                    "description": "Voucher type. Compare case-insensitively; 'Stock Transfer' = KACHA sale.",
                    "known_values": ["Sales", "Purchase", "Receipt", "Payment", "CreditNote", "DebitNote", "Journal",
                                     "Contra", "stock transfer", "stock inward", "purchase voucher", "Production", "Consumption"],
                },
                "TotalAmount": {"description": "Grand total incl. GST (revenue basis for Sales)."},
                "Date": {"description": "Voucher date (DATETIME); filter with DATE(v.Date)."},
                "PartyID": {"description": "Counter-party accounts.id."},
                "AccountID": {"description": "Ledger (cash/bank/sales) accounts.id."},
                "balance_amount": {"description": "Outstanding amount on this invoice."},
                "paid_amount": {"description": "Amount received/paid against this invoice."},
                "status": {"description": "Payment status ENUM(Pending, Partial, Paid)."},
                "staffid": {"description": "Assigned salesperson accounts.id."},
                "DC": {"description": "Debit/Credit marker: 'D' or 'C'."},
                "L_I": {"description": "local / interstate supply (drives CGST+SGST vs IGST)."},
                "staff_incentive": {"numeric_text": True},
            },
        },
        "voucherdetails": {
            "description": "Voucher line items (product, batch, qty, price, GST split).",
            "module_scope": ["sales_purchase", "inventory", "accounting"],
            "columns": {
                "product": {"description": "Product name (matches products.goods_name)."},
                "transaction_type": {"description": "Mirrors voucher.TransactionType for the line."},
                "batch": {"description": "batches.batch_number (with product_id)."},
                "total": {"description": "Line total incl. tax."},
            },
        },
    },
    "logical_joins": [
        {"left_table": "voucherdetails", "left_column": "voucher_id", "right_table": "voucher", "right_column": "VoucherID",
         "cardinality": "many_to_one", "description": "Voucher lines -> header (also declared FK)."},
        {"left_table": "order_items", "left_column": "order_number", "right_table": "orders", "right_column": "order_number",
         "cardinality": "many_to_one", "description": "Order lines -> order header (retailerScore.js)."},
        {"left_table": "voucher", "left_column": "PartyID", "right_table": "accounts", "right_column": "id",
         "cardinality": "many_to_one", "description": "Voucher counter-party (retailer/supplier)."},
        {"left_table": "voucher", "left_column": "AccountID", "right_table": "accounts", "right_column": "id",
         "cardinality": "many_to_one", "description": "Voucher ledger account (cash/bank/sales).",
         "alias_hint": "Use a distinct alias (e.g. acc) when joining accounts twice."},
        {"left_table": "voucher", "left_column": "staffid", "right_table": "accounts", "right_column": "id",
         "cardinality": "many_to_one", "description": "Assigned salesperson (salesReportRoutes.js)."},
        {"left_table": "voucherdetails", "left_column": "product_id", "right_table": "products", "right_column": "id",
         "cardinality": "many_to_one", "description": "Voucher line -> product (id-based)."},
        {"left_table": "voucherdetails", "left_column": "product", "right_table": "products", "right_column": "goods_name",
         "cardinality": "many_to_one", "description": "Name-based product join used by production COGS (profitLossRoutes.js L62).",
         "join_type_hint": "LEFT JOIN"},
        {"left_table": "batches", "left_column": "product_id", "right_table": "products", "right_column": "id",
         "cardinality": "many_to_one", "description": "Batch -> product (also declared FK)."},
        {"left_table": "voucherdetails", "left_column": "batch_id", "right_table": "batches", "right_column": "id",
         "cardinality": "many_to_one", "description": "Voucher line -> batch. Alternative: (product_id, batch) = (batches.product_id, batches.batch_number)."},
        {"left_table": "direct_deposit", "left_column": "voucher_id", "right_table": "voucher", "right_column": "VoucherID",
         "cardinality": "many_to_one", "description": "Bank deposit reconciled to a Receipt voucher."},
        {"left_table": "direct_deposit", "left_column": "party_id", "right_table": "accounts", "right_column": "id",
         "cardinality": "many_to_one", "description": "Bank deposit -> party (party_id is VARCHAR)."},
        {"left_table": "voucher", "left_column": "order_number", "right_table": "orders", "right_column": "order_number",
         "cardinality": "many_to_one", "description": "Invoice voucher generated from an app order."},
        {"left_table": "order_items", "left_column": "product_id", "right_table": "products", "right_column": "id",
         "cardinality": "many_to_one", "description": "Order line -> product (retailerScore.js L1422)."},
        {"left_table": "accounts", "left_column": "account_group_id", "right_table": "accountgroup", "right_column": "accountgroup_id",
         "cardinality": "many_to_one", "description": "Account -> chart-of-accounts group."},
        {"left_table": "products", "left_column": "category_id", "right_table": "categories", "right_column": "id",
         "cardinality": "many_to_one", "description": "Product -> category (category_id is VARCHAR)."},
        {"left_table": "products", "left_column": "company_id", "right_table": "companies", "right_column": "id",
         "cardinality": "many_to_one", "description": "Product -> brand company (company_id is VARCHAR)."},
        {"left_table": "expensive", "left_column": "staff_id", "right_table": "accounts", "right_column": "id",
         "cardinality": "many_to_one", "description": "Expense -> staff member."},
        {"left_table": "sales_visits", "left_column": "retailer_id", "right_table": "accounts", "right_column": "id",
         "cardinality": "many_to_one", "description": "Visit -> retailer."},
        {"left_table": "sales_visits", "left_column": "staff_id", "right_table": "accounts", "right_column": "id",
         "cardinality": "many_to_one", "description": "Visit -> staff member."},
        {"left_table": "cart_items", "left_column": "customer_id", "right_table": "accounts", "right_column": "id",
         "cardinality": "many_to_one", "description": "Cart line -> retailer."},
        {"left_table": "cart_items", "left_column": "product_id", "right_table": "products", "right_column": "id",
         "cardinality": "many_to_one", "description": "Cart line -> product."},
        {"left_table": "offers", "left_column": "product_id", "right_table": "products", "right_column": "id",
         "cardinality": "many_to_one", "description": "Product-scoped offer."},
        {"left_table": "offers", "left_column": "category_id", "right_table": "categories", "right_column": "id",
         "cardinality": "many_to_one", "description": "Category-scoped offer."},
    ],
    "extra_caveats": [
        {"table": "voucher", "column": "status", "issue": "ENUM_FILTER_MISMATCH", "severity": "low",
         "guidance": "status is ENUM('Pending','Partial','Paid'); production P&L still applies the "
                     "NOT IN ('Cancelled','canceled') filter. Keep it verbatim for reconciliation."},
        {"table": "voucher", "column": "TransactionType", "issue": "CASE_VARIANTS", "severity": "medium",
         "guidance": "Backend writes 'stock transfer' and reads 'Stock Transfer'; collation utf8mb4_general_ci makes "
                     "= comparisons case-insensitive. Never use BINARY comparisons."},
        {"table": "expensive", "column": None, "issue": "NO_PRIMARY_KEY", "severity": "low",
         "guidance": "Table has no PK; use COUNT(*) rather than COUNT(DISTINCT id) for expense counts."},
        {"table": "batches", "column": "exp_date", "issue": "DATE_STORED_AS_TEXT", "severity": "medium",
         "guidance": "Use STR_TO_DATE(b.exp_date, '%Y-%m-%d') for expiry comparisons."},
        {"table": None, "column": None, "issue": "RESERVED_WORD_COLUMNS", "severity": "medium",
         "guidance": "Back-quote reserved column names: accounts.`group`, expensive.`date`, voucher.`Date`."},
    ],
    "business_rules": [
        "Sales revenue = voucher.TransactionType = 'Sales' (PAKKA). 'stock transfer' vouchers are KACHA sales and are "
        "EXCLUDED from P&L revenue unless the user explicitly asks for kacha/stock-transfer sales.",
        "Stock-IN types: Purchase, CreditNote, stock inward. Stock-OUT types: Sales, DebitNote, stock transfer (VochurRoutes.js L1298).",
        "Sales returns = CreditNote vouchers; purchase returns = DebitNote vouchers.",
        "Retailers are accounts.role = 'retailer' (dual suppliers have is_dual_account = 1).",
        "Retailer purchase value = SUM(orders.net_payable) WHERE order_status != 'Cancelled' AND invoice_status = 1 (retailerScore.js).",
        "Indian financial year runs 1 April - 31 March; 'this FY' means DATE(v.Date) BETWEEN 'YYYY-04-01' AND 'YYYY+1-03-31'.",
        "All monetary values are INR.",
    ],
    "calculated_metrics": {
        "total_sales_revenue": {
            "description": "Gross sales revenue: sum of Sales voucher TotalAmount excluding cancelled.",
            "formula_type": "aggregate",
            "sql_expression": "COALESCE(SUM(v.TotalAmount), 0)",
            "required_tables": ["voucher"],
            "required_joins": [],
            "filters": ["v.TransactionType = 'Sales'", _NOT_CANCELLED_V],
            "date_filter": _DATE_V,
            "canonical_sql": _SALES_SQL,
            "source_reference": "ledger-backend/routes/profitLossRoutes.js#L37-L53",
        },
        "cost_of_goods_sold": {
            "description": "COGS: quantity sold x products.purchase_price, joined by product NAME as in production.",
            "formula_type": "aggregate",
            "sql_expression": "COALESCE(SUM(vd.quantity * COALESCE(p.purchase_price, 0)), 0)",
            "required_tables": ["voucherdetails", "voucher", "products"],
            "required_joins": ["voucher.VoucherID = voucherdetails.voucher_id", "products.goods_name = voucherdetails.product (LEFT JOIN)"],
            "filters": ["v.TransactionType = 'Sales'", _NOT_CANCELLED_V, "vd.transaction_type = 'Sales'"],
            "date_filter": _DATE_V,
            "canonical_sql": _COGS_SQL,
            "source_reference": "ledger-backend/routes/profitLossRoutes.js#L57-L76",
        },
        "total_expenses": {
            "description": "Operating expenses from the expensive table excluding cancelled.",
            "formula_type": "aggregate",
            "sql_expression": "COALESCE(SUM(e.amount), 0)",
            "required_tables": ["expensive"],
            "required_joins": [],
            "filters": [_NOT_CANCELLED_E],
            "date_filter": _DATE_E,
            "canonical_sql": _EXPENSES_SQL,
            "source_reference": "ledger-backend/routes/profitLossRoutes.js#L98-L113",
        },
        "gross_profit": {
            "description": "Gross profit = total_sales_revenue - cost_of_goods_sold.",
            "formula_type": "derived",
            "sql_expression": "total_sales_revenue - cost_of_goods_sold",
            "depends_on": ["total_sales_revenue", "cost_of_goods_sold"],
            "required_tables": ["voucher", "voucherdetails", "products"],
            "required_joins": [],
            "filters": [],
            "canonical_sql": (
                f"WITH sales AS ({_SALES_SQL}), cogs AS ({_COGS_SQL}) "
                "SELECT s.total_sales_revenue, c.cost_of_goods_sold, s.total_sales_revenue - c.cost_of_goods_sold AS gross_profit "
                "FROM sales s CROSS JOIN cogs c"
            ),
            "source_reference": "ledger-backend/routes/profitLossRoutes.js#L177-L178",
        },
        "net_profit": {
            "description": "Net profit = gross_profit - total_expenses (positive = PROFIT, negative = LOSS).",
            "formula_type": "derived",
            "sql_expression": "total_sales_revenue - cost_of_goods_sold - total_expenses",
            "depends_on": ["gross_profit", "total_expenses"],
            "required_tables": ["voucher", "voucherdetails", "products", "expensive"],
            "required_joins": [],
            "filters": [],
            "canonical_sql": _PL_CTE,
            "source_reference": "ledger-backend/routes/profitLossRoutes.js#L180-L203",
        },
        "party_unpaid_balance": {
            "description": "Outstanding receivable per party from accounts.unpaid_amount (VARCHAR -> DECIMAL).",
            "formula_type": "aggregate",
            "sql_expression": "COALESCE(CAST(a.unpaid_amount AS DECIMAL(15,2)), 0)",
            "required_tables": ["accounts"],
            "required_joins": [],
            "filters": ["CAST(a.unpaid_amount AS DECIMAL(15,2)) > 0"],
            "canonical_sql": (
                "SELECT a.id, a.name, a.business_name, a.role, "
                "COALESCE(CAST(a.unpaid_amount AS DECIMAL(15,2)), 0) AS party_unpaid_balance, a.credit_limit "
                "FROM accounts a WHERE CAST(a.unpaid_amount AS DECIMAL(15,2)) > 0 "
                "ORDER BY party_unpaid_balance DESC"
            ),
            "alternative": "Invoice-level: SUM(v.balance_amount) FROM voucher v WHERE v.TransactionType = 'Sales' GROUP BY v.PartyID (retailerScore.js L929-L941).",
            "source_reference": "ledger-backend/routes/VochurRoutes.js#L1396-L1440",
        },
        "total_purchases": {
            "description": "Purchase voucher totals (reference only; NOT used as COGS).",
            "formula_type": "aggregate",
            "sql_expression": "COALESCE(SUM(v.TotalAmount), 0)",
            "required_tables": ["voucher"],
            "required_joins": [],
            "filters": ["v.TransactionType = 'Purchase'", _NOT_CANCELLED_V],
            "date_filter": _DATE_V,
            "canonical_sql": _PURCHASE_SQL,
            "source_reference": "ledger-backend/routes/profitLossRoutes.js#L79-L95",
        },
        "batch_stock_on_hand": {
            "description": "Live on-hand stock and cost valuation per batch (VARCHAR stock/cost columns cast).",
            "formula_type": "aggregate",
            "sql_expression": "CAST(b.quantity AS DECIMAL(15,2))",
            "required_tables": ["batches", "products"],
            "required_joins": ["batches.product_id = products.id"],
            "filters": [],
            "canonical_sql": (
                "SELECT p.goods_name, b.batch_number, b.exp_date, CAST(b.quantity AS DECIMAL(15,2)) AS stock_on_hand, "
                "CAST(b.quantity AS DECIMAL(15,2)) * CAST(b.purchase_price AS DECIMAL(15,2)) AS stock_value "
                "FROM batches b JOIN products p ON p.id = b.product_id ORDER BY p.goods_name, b.batch_number"
            ),
            "source_reference": "ledger-backend/routes/VochurRoutes.js#L1297-L1345",
        },
    },
}
