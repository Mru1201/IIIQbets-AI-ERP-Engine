"""Metastore loader and module-scope pruner.

Pruning keeps prompts small and grounded: only tables in the requested module
scopes are injected, along with joins whose *both* ends survive and metrics
whose required tables are all present.
"""

from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from app.config import DEFAULT_METASTORE_PATH

MODULE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "accounting": ("ledger", "account", "party", "parties", "retailer", "supplier", "debtor", "creditor",
                   "receivable", "payable", "outstanding", "unpaid", "balance", "voucher", "journal", "receipt"),
    "inventory": ("stock", "inventory", "batch", "product", "item", "sku", "expiry", "expire", "category",
                  "brand", "company", "unit", "godown", "warehouse", "offer"),
    "sales_purchase": ("sale", "sales", "revenue", "invoice", "purchase", "gst", "tax", "cgst", "sgst", "igst",
                       "credit note", "debit note", "return", "turnover", "cogs", "profit", "loss", "p&l", "margin"),
    "orders": ("order", "cart", "visit", "salesperson", "field", "credit period", "approval"),
    "finance_expenses": ("expense", "expenses", "spend", "petrol", "diesel", "reimburse", "deposit", "bank",
                         "profit", "loss", "p&l", "net profit", "cash flow"),
}


class MetastoreError(ValueError):
    pass


@lru_cache(maxsize=8)
def _load_cached(path_str: str, mtime: float) -> str:  # mtime busts the cache on regeneration
    return Path(path_str).read_text(encoding="utf-8")


def load_metastore(path: str | Path = DEFAULT_METASTORE_PATH) -> dict[str, Any]:
    """Load and structurally validate the tenant metastore (returns a fresh copy)."""
    p = Path(path)
    if not p.exists():
        raise MetastoreError(f"Metastore not found at {p}. Run `python -m app.connectors.introspector`.")
    data = json.loads(_load_cached(str(p.resolve()), p.stat().st_mtime))
    for key in ("tenant_id", "sql_dialect", "tables", "allowed_joins", "calculated_metrics", "security"):
        if key not in data:
            raise MetastoreError(f"Metastore missing required key '{key}'")
    return data


def infer_modules(question: str) -> list[str]:
    """Cheap deterministic keyword router: question -> module scopes (empty = all)."""
    q = question.lower()
    hits = [m for m, kws in MODULE_KEYWORDS.items() if any(re.search(rf"\b{re.escape(k)}", q) for k in kws)]
    return sorted(hits)


def prune_metastore(
    metastore: dict[str, Any],
    modules: Iterable[str] | None = None,
    tables: Iterable[str] | None = None,
    always_include: Iterable[str] = ("accounts",),
) -> dict[str, Any]:
    """Return a copy of ``metastore`` restricted to ``modules`` and/or explicit ``tables``.

    If neither is given (or nothing matches), the full metastore is returned.
    Metrics are kept only if every required table survives; joins only if both ends survive.
    """
    modules = set(modules or [])
    explicit = set(tables or [])
    if "modules" in metastore and modules:
        unknown_modules = modules - set(metastore.get("modules", {}))
        if unknown_modules:
            raise MetastoreError(f"Unknown module scope(s): {sorted(unknown_modules)}")

    if not modules and not explicit:
        return copy.deepcopy(metastore)

    keep = {t for t, meta in metastore["tables"].items() if modules & set(meta.get("module_scope", []))}
    keep |= explicit & set(metastore["tables"])
    if keep:
        keep |= set(always_include) & set(metastore["tables"])
    else:
        return copy.deepcopy(metastore)

    # Pull in tables required by metrics touching any kept table so formulas stay executable.
    for m in metastore.get("calculated_metrics", {}).values():
        req = set(m.get("required_tables", []))
        if req & keep and modules & {"sales_purchase", "finance_expenses"} and len(req - keep) <= 2:
            keep |= req

    out = copy.deepcopy(metastore)
    out["tables"] = {t: v for t, v in metastore.get("tables", {}).items() if t in keep}
    out["allowed_joins"] = [
        j for j in metastore.get("allowed_joins", [])
        if j.get("left_table") in keep and j.get("right_table") in keep
    ]
    out["calculated_metrics"] = {
        k: v for k, v in metastore.get("calculated_metrics", {}).items()
        if set(v.get("required_tables", [])) <= keep
    }
    out["data_caveats"] = [c for c in metastore.get("data_caveats", []) if c.get("table") in keep or c.get("table") is None]
    out["modules"] = {k: v for k, v in metastore.get("modules", {}).items() if not modules or k in modules}
    out["pruned_scope"] = {"modules": sorted(modules), "tables": sorted(keep)}
    return copy.deepcopy(out)


def column_index(metastore: dict[str, Any]) -> dict[str, set[str]]:
    """``{table_lower: {column_lower, ...}}`` for whitelist validation."""
    return {t.lower(): {c.lower() for c in meta["columns"]} for t, meta in metastore["tables"].items()}
