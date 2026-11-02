"""Red-team security tests and verification suite for SQLGlot AST Security Gate."""

from __future__ import annotations

import pytest

from app.metastore.loader import load_metastore
from app.security.guardrails import (
    SQLSecurityViolation,
    referenced_tables,
    validate_and_sanitize_sql,
)


@pytest.fixture(scope="module")
def metastore() -> dict:
    return load_metastore()


# =========================================================================== #
# 1. 100% BLOCK RATE ON RED-TEAM ATTACKS & INJECTIONS
# =========================================================================== #

@pytest.mark.parametrize(
    "attack_sql, expected_code",
    [
        # Stacked SQL Injection
        ("SELECT * FROM voucher; DROP TABLE accounts;", "MULTIPLE_STATEMENTS"),
        ("SELECT 1; SELECT 2;", "MULTIPLE_STATEMENTS"),
        ("SELECT * FROM accounts; TRUNCATE TABLE batches;", "MULTIPLE_STATEMENTS"),
        ("SELECT * FROM accounts WHERE id = 1; UPDATE accounts SET unpaid_amount = 0;", "MULTIPLE_STATEMENTS"),
        
        # DDL statements
        ("DROP TABLE accounts", "FORBIDDEN_STATEMENT"),
        ("CREATE TABLE backdoor (id INT)", "FORBIDDEN_STATEMENT"),
        ("ALTER TABLE accounts ADD COLUMN evil VARCHAR(255)", "FORBIDDEN_STATEMENT"),
        ("TRUNCATE TABLE batches", "FORBIDDEN_STATEMENT"),
        ("RENAME TABLE accounts TO accounts_backup", "FORBIDDEN_STATEMENT"),

        # DML statements
        ("DELETE FROM voucher WHERE 1=1", "FORBIDDEN_STATEMENT"),
        ("UPDATE accounts SET unpaid_amount = '0.00' WHERE id = 1", "FORBIDDEN_STATEMENT"),
        ("INSERT INTO expensive (staff_name, amount) VALUES ('Eve', 99999)", "FORBIDDEN_STATEMENT"),
        ("REPLACE INTO products (id, goods_name) VALUES (1, 'Hacked')", "FORBIDDEN_STATEMENT"),

        # DCL / Privilege escalation
        ("GRANT ALL PRIVILEGES ON *.* TO 'attacker'@'%'", "FORBIDDEN_STATEMENT"),
        ("REVOKE SELECT ON ledger.* FROM 'ai_reader'@'%'", "FORBIDDEN_STATEMENT"),

        # Administrative / Session / Transaction statements
        ("SET @a = 1", "FORBIDDEN_STATEMENT"),
        ("SHOW TABLES", "FORBIDDEN_STATEMENT"),
        ("LOCK TABLES accounts READ", "FORBIDDEN_STATEMENT"),
        ("START TRANSACTION", "FORBIDDEN_STATEMENT"),
        ("COMMIT", "FORBIDDEN_STATEMENT"),
        ("USE mysql", "FORBIDDEN_STATEMENT"),
        ("KILL 1234", "FORBIDDEN_STATEMENT"),

        # CTE-wrapped DML attacks
        ("WITH d AS (DELETE FROM accounts RETURNING *) SELECT * FROM d", "FORBIDDEN_OPERATION"),
        ("WITH u AS (UPDATE accounts SET unpaid_amount = 0 RETURNING *) SELECT * FROM u", "FORBIDDEN_OPERATION"),
        ("WITH i AS (INSERT INTO expensive (amount) VALUES (100) RETURNING *) SELECT * FROM i", "FORBIDDEN_OPERATION"),

        # Restricted credential tables
        ("SELECT * FROM gsp_credentials", "RESTRICTED_TABLE"),
        ("SELECT username, password FROM gsp_credentials", "RESTRICTED_TABLE"),
        ("SELECT * FROM gsp_api", "RESTRICTED_TABLE"),
        ("SELECT access_token FROM gsp_api", "RESTRICTED_TABLE"),
        ("WITH gsp_credentials AS (SELECT 1 AS id) SELECT * FROM gsp_credentials", "RESTRICTED_TABLE"),
        ("SELECT a.id, g.username FROM accounts a JOIN gsp_credentials g ON 1=1", "RESTRICTED_TABLE"),
        ("SELECT (SELECT username FROM gsp_credentials LIMIT 1) FROM accounts", "RESTRICTED_TABLE"),

        # Excluded internal tables
        ("SELECT * FROM chatbot_audit_log", "RESTRICTED_TABLE"),

        # Cross-database and system schemas
        ("SELECT * FROM mysql.user", "CROSS_DATABASE"),
        ("SELECT * FROM information_schema.tables", "CROSS_DATABASE"),
        ("SELECT * FROM performance_schema.threads", "CROSS_DATABASE"),
        ("SELECT * FROM sys.version", "CROSS_DATABASE"),
        ("SELECT * FROM other_db.accounts", "CROSS_DATABASE"),

        # Exfiltration via file operations
        ("SELECT LOAD_FILE('/etc/passwd')", "FORBIDDEN_FUNCTION"),
        ("SELECT * FROM accounts INTO OUTFILE '/tmp/dump.csv'", "FORBIDDEN_OPERATION"),
        ("SELECT * FROM accounts INTO DUMPFILE '/tmp/dump.bin'", "FORBIDDEN_OPERATION"),

        # Timing / DoS / Fingerprinting functions
        ("SELECT SLEEP(5)", "FORBIDDEN_FUNCTION"),
        ("SELECT BENCHMARK(50000000, MD5('attack'))", "FORBIDDEN_FUNCTION"),
        ("SELECT GET_LOCK('test', 10)", "FORBIDDEN_FUNCTION"),
        ("SELECT USER(), DATABASE(), VERSION()", "FORBIDDEN_FUNCTION"),
        ("SELECT CURRENT_USER()", "FORBIDDEN_FUNCTION"),
        ("SELECT @@version", "FORBIDDEN_FUNCTION"),
        ("SELECT @user_variable", "FORBIDDEN_FUNCTION"),

        # Error-based exfiltration functions
        ("SELECT EXTRACTVALUE(1, CONCAT('~', (SELECT 'secret')))", "FORBIDDEN_FUNCTION"),
        ("SELECT UPDATEXML(1, CONCAT('~', (SELECT 'secret')), 1)", "FORBIDDEN_FUNCTION"),

        # Executable comment smuggling
        ("SELECT /*! 1 */ 2 FROM accounts", "EXECUTABLE_COMMENT"),
        ("SELECT /*!50000 1, */ id FROM accounts", "EXECUTABLE_COMMENT"),
        ("SELECT /*!M! 1 */ FROM accounts", "EXECUTABLE_COMMENT"),

        # Hallucinated tables
        ("SELECT * FROM ledger_entries", "UNKNOWN_TABLE"),
        ("SELECT * FROM users", "UNKNOWN_TABLE"),
        ("SELECT * FROM payments", "UNKNOWN_TABLE"),

        # Hallucinated columns
        ("SELECT secret_notes FROM accounts", "UNKNOWN_COLUMN"),
        ("SELECT a.fake_column FROM accounts a", "UNKNOWN_COLUMN"),
        ("SELECT v.total_revenue FROM voucher v", "UNKNOWN_COLUMN"),
        ("SELECT p.supplier_code FROM products p", "UNKNOWN_COLUMN"),

        # Stripped credential/PII columns in accounts
        ("SELECT password FROM accounts", "UNKNOWN_COLUMN"),
        ("SELECT otp FROM accounts", "UNKNOWN_COLUMN"),
        ("SELECT aadhaar_number FROM accounts", "UNKNOWN_COLUMN"),
        ("SELECT bank_account_number FROM accounts", "UNKNOWN_COLUMN"),
        ("SELECT upi_id FROM accounts", "UNKNOWN_COLUMN"),

        # Stripped binary columns in voucher
        ("SELECT pdf_data FROM voucher", "UNKNOWN_COLUMN"),

        # Empty / whitespace / invalid
        ("", "EMPTY_QUERY"),
        ("   \n\t  ", "EMPTY_QUERY"),
        ("SELECT * FROM", "PARSE_ERROR"),
    ],
)
def test_red_team_attacks_blocked(metastore: dict, attack_sql: str, expected_code: str):
    """Verify 100% block rate on all red-team attack vectors."""
    with pytest.raises(SQLSecurityViolation) as exc_info:
        validate_and_sanitize_sql(attack_sql, metastore)
    assert exc_info.value.code == expected_code


# =========================================================================== #
# 2. STAR EXPANSION & SECRET COLUMN ELIMINATION
# =========================================================================== #

def test_star_expansion_strips_sensitive_columns(metastore: dict):
    """Ensure SELECT * FROM accounts expands to whitelisted columns, never secrets."""
    sanitized = validate_and_sanitize_sql("SELECT * FROM accounts", metastore)
    lower = sanitized.lower()
    
    # Must contain whitelisted columns
    assert "accounts.`id`" in sanitized or "`id`" in sanitized
    assert "name" in lower
    assert "business_name" in lower

    # Must NOT contain stripped columns
    for secret in ("password", "otp", "aadhaar_number", "bank_account_number", "upi_id"):
        assert secret not in lower


def test_aliased_star_expansion(metastore: dict):
    """Ensure SELECT a.*, v.VoucherID expands safely without leaking sensitive columns."""
    query = "SELECT a.*, v.VoucherID FROM accounts a JOIN voucher v ON v.PartyID = a.id"
    sanitized = validate_and_sanitize_sql(query, metastore)
    lower = sanitized.lower()
    assert "password" not in lower
    assert "otp" not in lower
    assert "a.`id`" in sanitized or "a.id" in lower


# =========================================================================== #
# 3. LIMIT ENFORCEMENT & CAP
# =========================================================================== #

def test_limit_injection_when_omitted(metastore: dict):
    """Queries without a LIMIT clause automatically receive LIMIT 1000."""
    query = "SELECT id, name FROM accounts WHERE role = 'retailer'"
    sanitized = validate_and_sanitize_sql(query, metastore, max_rows=1000)
    assert "LIMIT 1000" in sanitized


def test_limit_preserved_when_under_max(metastore: dict):
    """LIMIT clauses below the maximum row cap are preserved."""
    query = "SELECT id, name FROM accounts LIMIT 25"
    sanitized = validate_and_sanitize_sql(query, metastore, max_rows=1000)
    assert "LIMIT 25" in sanitized


def test_limit_capped_when_exceeding_max(metastore: dict):
    """LIMIT clauses exceeding max_rows are capped at max_rows."""
    query = "SELECT id, name FROM accounts LIMIT 5000"
    sanitized = validate_and_sanitize_sql(query, metastore, max_rows=1000)
    assert "LIMIT 1000" in sanitized
    assert "5000" not in sanitized


# =========================================================================== #
# 4. VALIDATED PASS-THROUGH ON COMPLEX PRODUCTION QUERIES
# =========================================================================== #

def test_sales_report_complex_query(metastore: dict):
    """Verify sales report query with joins, grouping, and aggregations passes cleanly."""
    query = """
    SELECT 
      MIN(vd.id) AS id,
      vd.product,
      vd.product_id,
      vd.batch,
      DATE(v.Date) AS invoice_date,
      v.order_number,
      v.order_mode,
      v.Subtotal,
      v.TransactionType,
      v.PartyName AS retailer,
      a.name AS assigned_staff,
      SUM(vd.quantity) AS quantity,
      SUM(vd.price) AS price,
      SUM(vd.discount) AS discount,
      SUM(vd.gst) AS gst,
      SUM(vd.total) AS total,
      CASE 
        WHEN v.TransactionType = 'Sales' THEN 'pakka'
        WHEN v.TransactionType = 'stock transfer' THEN 'kacha'
        ELSE 'other'
      END AS sales_type
    FROM voucherdetails vd
    LEFT JOIN voucher v ON vd.voucher_id = v.VoucherID
    LEFT JOIN accounts a ON v.staffid = a.id
    WHERE v.TransactionType IN ('Sales', 'stock transfer')
    GROUP BY 
      vd.product_id,
      vd.batch,
      vd.product,
      v.PartyName,
      a.name,
      v.TransactionType,
      v.order_number,
      v.Subtotal,
      DATE(v.Date),
      sales_type
    ORDER BY invoice_date DESC
    """
    sanitized = validate_and_sanitize_sql(query, metastore)
    assert "FROM voucherdetails" in sanitized
    assert "LIMIT 1000" in sanitized


def test_profit_and_loss_with_ctes(metastore: dict):
    """Verify full production P&L calculation with CTEs passes the security gate."""
    query = """
    WITH sales AS (
      SELECT COALESCE(SUM(v.TotalAmount), 0) AS total_sales_revenue,
             COUNT(v.VoucherID) AS sales_invoice_count
      FROM voucher v
      WHERE v.TransactionType = 'Sales'
        AND (v.status NOT IN ('Cancelled', 'canceled') OR v.status IS NULL OR v.status = '')
        AND DATE(v.Date) BETWEEN '2026-04-01' AND '2027-03-31'
    ),
    cogs AS (
      SELECT COALESCE(SUM(vd.quantity * COALESCE(p.purchase_price, 0)), 0) AS cost_of_goods_sold,
             COALESCE(SUM(vd.quantity), 0) AS total_units_sold
      FROM voucherdetails vd
      JOIN voucher v ON v.VoucherID = vd.voucher_id
      LEFT JOIN products p ON p.goods_name = vd.product
      WHERE v.TransactionType = 'Sales'
        AND (v.status NOT IN ('Cancelled', 'canceled') OR v.status IS NULL OR v.status = '')
        AND vd.transaction_type = 'Sales'
        AND DATE(v.Date) BETWEEN '2026-04-01' AND '2027-03-31'
    ),
    expenses AS (
      SELECT COALESCE(SUM(e.amount), 0) AS total_expenses,
             COUNT(*) AS expense_count
      FROM expensive e
      WHERE (e.status NOT IN ('Cancelled', 'canceled') OR e.status IS NULL OR e.status = '')
        AND DATE(e.`date`) BETWEEN '2026-04-01' AND '2027-03-31'
    )
    SELECT 
      s.total_sales_revenue,
      c.cost_of_goods_sold,
      s.total_sales_revenue - c.cost_of_goods_sold AS gross_profit,
      x.total_expenses,
      s.total_sales_revenue - c.cost_of_goods_sold - x.total_expenses AS net_profit
    FROM sales s
    CROSS JOIN cogs c
    CROSS JOIN expenses x
    """
    sanitized = validate_and_sanitize_sql(query, metastore)
    assert "WITH sales AS" in sanitized
    assert "gross_profit" in sanitized
    assert "net_profit" in sanitized
    assert "LIMIT 1000" in sanitized


def test_batch_stock_report_with_cast(metastore: dict):
    """Verify live on-hand batch stock valuation with VARCHAR cast passes."""
    query = """
    SELECT 
      p.goods_name,
      b.batch_number,
      b.exp_date,
      CAST(b.quantity AS DECIMAL(15,2)) AS stock_on_hand,
      CAST(b.purchase_price AS DECIMAL(15,2)) AS unit_cost,
      CAST(b.quantity AS DECIMAL(15,2)) * CAST(b.purchase_price AS DECIMAL(15,2)) AS stock_value
    FROM batches b
    JOIN products p ON p.id = b.product_id
    WHERE CAST(b.quantity AS DECIMAL(15,2)) > 0
    ORDER BY p.goods_name, b.batch_number
    """
    sanitized = validate_and_sanitize_sql(query, metastore)
    assert "batches" in sanitized
    assert "products" in sanitized
    assert "LIMIT 1000" in sanitized


def test_unpaid_balance_cast_query(metastore: dict):
    """Verify party unpaid receivable calculation with VARCHAR cast passes."""
    query = """
    SELECT 
      a.id,
      a.name,
      a.business_name,
      a.role,
      COALESCE(CAST(a.unpaid_amount AS DECIMAL(15,2)), 0) AS party_unpaid_balance,
      a.credit_limit
    FROM accounts a
    WHERE CAST(a.unpaid_amount AS DECIMAL(15,2)) > 0
    ORDER BY party_unpaid_balance DESC
    """
    sanitized = validate_and_sanitize_sql(query, metastore)
    assert "party_unpaid_balance" in sanitized
    assert "accounts" in sanitized
    assert "LIMIT 1000" in sanitized


def test_union_query_pass(metastore: dict):
    """Verify UNION query between retailer and supplier accounts passes and receives LIMIT."""
    query = """
    SELECT id, name, role FROM accounts WHERE role = 'retailer'
    UNION
    SELECT id, name, role FROM accounts WHERE role = 'supplier'
    """
    sanitized = validate_and_sanitize_sql(query, metastore)
    assert "UNION" in sanitized
    assert "LIMIT 1000" in sanitized


def test_correlated_subquery_pass(metastore: dict):
    """Verify correlated subquery across accounts and orders passes."""
    query = """
    SELECT 
      a.id,
      a.name,
      (SELECT COALESCE(SUM(o.net_payable), 0)
       FROM orders o
       WHERE o.customer_id = a.id
         AND o.order_status != 'Cancelled') AS total_order_value
    FROM accounts a
    WHERE a.role = 'retailer'
    """
    sanitized = validate_and_sanitize_sql(query, metastore)
    assert "total_order_value" in sanitized
    assert "LIMIT 1000" in sanitized


def test_referenced_tables_helper():
    """Verify physical table extractor correctly filters out CTE names."""
    sql = "WITH my_cte AS (SELECT id FROM accounts) SELECT c.id, v.TotalAmount FROM my_cte c JOIN voucher v ON 1=1"
    tables = referenced_tables(sql)
    assert tables == ["accounts", "voucher"]
    assert "my_cte" not in tables
