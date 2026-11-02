# IIIQbets Pluggable AI ERP Reporting Engine (`erp-ai-orchestrator`)

**Phase 1:** Data Plane, Semantic Metastore, Constrained Inference Contract, and SQLGlot AST Security Gate.

Strictly read-only, headless Text-to-SQL sidecar tailored for testbed client **Shree Shashwatraj Agro Pvt Ltd** (`ledger` MariaDB 10.4 / MySQL 8 database).

---

## 1. Architecture Overview

```text
erp-ai-orchestrator/
├── app/
│   ├── config.py                 # Pydantic environment settings (ERP_ prefix)
│   ├── connectors/
│   │   └── introspector.py       # Live DB (SQLAlchemy) & fallback SQL Dump parser
│   ├── metastore/
│   │   ├── semantic_overlay.py   # Ground-truth formulas & logical join definitions
│   │   ├── metastore_schema.json # Enriched tenant metastore (20 tables, 26 joins, 8 metrics)
│   │   └── loader.py             # Metastore loader and module-scope pruner
│   ├── schemas/
│   │   └── report_plan.py        # Constrained Pydantic ReportPlanSchema contract
│   ├── security/
│   │   └── guardrails.py         # Deterministic SQLGlot AST Security Gate
│   └── inference/
│       ├── prompts.py            # System prompt builder with schema & formula injection
│       └── llm_client.py         # Constrained JSON inference client (vLLM / Ollama / OpenAI)
├── tests/
│   ├── test_guardrails.py        # 100% block rate red-team injection & whitelist verification
│   └── test_introspector.py      # Metastore completeness, FKs, caveats & formula tests
├── requirements.txt
└── README.md
```

---

## 2. Security Gate Model (`app/security/guardrails.py`)

Every generated SQL query passes through `validate_and_sanitize_sql(...)` before it can ever touch a database connection. The gate operates **fail-closed**:

1. **Lexical Pre-checks**:
   - Rejects empty, whitespace, or oversized queries (> 20,000 characters).
   - Rejects MySQL executable comment smuggling (`/*! ... */`, `/*!M! ... */`).
   - Rejects file exfiltration commands (`INTO OUTFILE`, `INTO DUMPFILE`).
2. **Strict Single-Statement**:
   - `len(sqlglot.parse(raw_sql)) == 1`. Blocks stacked SQL injections (e.g. `SELECT * FROM voucher; DROP TABLE accounts;`).
3. **Root Node Enforcement**:
   - Only `exp.Select` or `exp.Union` are permitted as root AST nodes.
4. **AST Walk for Prohibited Operations**:
   - Blocks DDL: `Drop`, `Create`, `Alter`, `TruncateTable`, `RenameTable`.
   - Blocks DML: `Insert`, `Update`, `Delete`, `Replace`, `Merge` (including CTE-wrapped DML).
   - Blocks DCL & Admin: `Grant`, `Revoke`, `Lock`, `Set`, `Show`, `Kill`, `Use`, `Transaction`.
5. **Forbidden Function Blacklist**:
   - Timing / DoS: `SLEEP()`, `BENCHMARK()`, `GET_LOCK()`.
   - File & OS: `LOAD_FILE()`, `SYS_EXEC()`.
   - Fingerprinting: `USER()`, `DATABASE()`, `VERSION()`, `@@version`, `@variables`.
   - Error-based exfiltration: `EXTRACTVALUE()`, `UPDATEXML()`.
6. **Table & Schema Whitelisting**:
   - Cross-database queries blocked (e.g. `mysql.user`, `information_schema.tables`).
   - Blocks access to credential stores: `gsp_credentials`, `gsp_api`.
   - Blocks access to internal logs: `chatbot_audit_log`.
7. **Star Projection Expansion**:
   - `SELECT *` and `SELECT alias.*` are rewritten into explicit, metastore-whitelisted columns. Secret columns (`accounts.password`, `accounts.otp`, `accounts.aadhaar_number`, `voucher.pdf_data`) are never projected.
8. **Scope-Aware Column Whitelisting**:
   - Verifies all referenced columns against the tenant metastore across CTEs, subqueries, aliases, and joins.
9. **Enforced Limit (Max 1000 Rows)**:
   - Queries without `LIMIT` receive `LIMIT 1000`.
   - Existing limits greater than 1000 are capped to 1000.

---

## 3. Ground-Truth Financial Metrics Codification

Formulas in `app/metastore/semantic_overlay.py` are codified verbatim from `ledger-backend/routes/profitLossRoutes.js`, `VochurRoutes.js`, and `retailerScore.js`:

| Metric | Business Formula / Production SQL Ground Truth |
| :--- | :--- |
| **`total_sales_revenue`** | `COALESCE(SUM(v.TotalAmount), 0)` from `voucher v` where `TransactionType = 'Sales'` and status NOT IN ('Cancelled', 'canceled'). *(Excludes KACHA stock transfers)*. |
| **`cost_of_goods_sold`** | `COALESCE(SUM(vd.quantity * COALESCE(p.purchase_price, 0)), 0)` from `voucherdetails vd` joined with `voucher v` and `LEFT JOIN products p ON p.goods_name = vd.product` where `v.TransactionType = 'Sales'` and `vd.transaction_type = 'Sales'`. |
| **`total_expenses`** | `COALESCE(SUM(e.amount), 0)` from `expensive e` where status NOT IN ('Cancelled', 'canceled'). |
| **`gross_profit`** | `total_sales_revenue - cost_of_goods_sold` (via CTE). |
| **`net_profit`** | `gross_profit - total_expenses` (via CTE). |
| **`party_unpaid_balance`** | `COALESCE(CAST(a.unpaid_amount AS DECIMAL(15,2)), 0)` from `accounts a` where `unpaid_amount > 0`. |
| **`batch_stock_on_hand`** | `CAST(b.quantity AS DECIMAL(15,2))` on-hand stock and `CAST(b.quantity AS DECIMAL(15,2)) * CAST(b.purchase_price AS DECIMAL(15,2))` valuation. |

### Data Caveat Highlights
- **VARCHAR Decimals**: `accounts.unpaid_amount`, `accounts.opening_balance`, `batches.quantity`, and `batches.purchase_price` are stored as `VARCHAR` in MariaDB. Metastore flags them with `NUMERIC_STORED_AS_TEXT` and mandates `CAST(... AS DECIMAL(15,2))`.
- **Reserved Keywords**: `accounts.\`group\``, `expensive.\`date\``, `voucher.\`Date\`` are back-quoted.

---

## 4. Setup and Verification

### Virtual Environment & Requirements
```bash
cd erp-ai-orchestrator
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Run Schema Introspector
```bash
# Introspect schema from ./ledger (34).sql fallback and write metastore_schema.json:
python -m app.connectors.introspector

# With live database connection:
python -m app.connectors.introspector --db-url "mysql+pymysql://ai_reader:secret@localhost:3306/ledger"
```

### Run Phase 1 Pytest Suite
```bash
pytest -v
```
**95 out of 95 tests passing**, covering:
- Red-team SQL injection, DDL/DML, CTE bypasses, and credential access.
- Star expansion and secret column stripping.
- Hallucinated table and column rejection.
- Production P&L CTEs, Sales Reports, Batch Stock, and Unpaid Balance queries.
- Introspector table cataloging, declared and logical joins, and metastore pruning.
