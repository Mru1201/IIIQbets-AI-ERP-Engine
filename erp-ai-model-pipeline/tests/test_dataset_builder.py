"""Tests for Synthetic Dataset Builder (Feature 28)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlglot
from sqlglot import exp

from dataset.bird_converter import generate_bird_samples
from dataset.build_dataset import build_dataset
from dataset.erp_synthetic import generate_erp_synthetic_samples
from dataset.refusal_generator import generate_refusal_samples
from dataset.validator import validate_dataset_records, validate_single_record


def test_bird_converter_generation() -> None:
    samples = generate_bird_samples(count=50, seed=42)
    assert len(samples) == 50
    for s in samples:
        is_valid, err = validate_single_record(s)
        assert is_valid is True, f"Validation failed: {err}"
        plan = json.loads(s["messages"][2]["content"])
        assert plan["sql_query"] is not None
        assert plan["error"] is None
        assert len(plan["metrics_applied"]) > 0
        parsed = sqlglot.parse_one(plan["sql_query"], read="mysql")
        ast_tables = {t.name.lower() for t in parsed.find_all(exp.Table) if t.name}
        assert set(t.lower() for t in plan["tables_used"]) == ast_tables


def test_erp_synthetic_generation() -> None:
    samples = generate_erp_synthetic_samples(count=50, seed=43)
    assert len(samples) == 50
    for s in samples:
        is_valid, err = validate_single_record(s)
        assert is_valid is True, f"Validation failed: {err}"
        plan = json.loads(s["messages"][2]["content"])
        assert plan["sql_query"] is not None
        assert plan["error"] is None
        assert len(plan["tables_used"]) >= 1
        parsed = sqlglot.parse_one(plan["sql_query"], read="mysql")
        ast_tables = {t.name.lower() for t in parsed.find_all(exp.Table) if t.name}
        assert set(t.lower() for t in plan["tables_used"]) == ast_tables


def test_refusal_generator() -> None:
    samples = generate_refusal_samples(count=50, seed=44)
    assert len(samples) == 50
    for s in samples:
        is_valid, err = validate_single_record(s)
        assert is_valid is True, f"Validation failed: {err}"
        plan = json.loads(s["messages"][2]["content"])
        # Mutual exclusivity: sql_query MUST be None, error MUST be string
        assert plan["sql_query"] is None
        assert plan["error"] is not None
        assert len(plan["error"]) > 10


def test_build_dataset_full_scale(tmp_path: Path) -> None:
    out_dir = tmp_path / "dataset_out"
    summary = build_dataset(
        total_samples=300,  # Scaled for fast unit testing
        bird_ratio=0.70,
        gretel_ratio=0.20,
        refusal_ratio=0.10,
        split_ratio=0.90,
        seed=100,
        output_dir=out_dir,
    )

    assert summary["total_samples"] == 300
    assert summary["train_samples"] == 270
    assert summary["val_samples"] == 30
    assert summary["bird_count"] == 210
    assert summary["gretel_count"] == 60
    assert summary["refusal_count"] == 30

    train_file = Path(summary["train_path"])
    val_file = Path(summary["val_path"])
    assert train_file.exists()
    assert val_file.exists()

    train_lines = train_file.read_text(encoding="utf-8").strip().split("\n")
    val_lines = val_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(train_lines) == 270
    assert len(val_lines) == 30


def test_validator_rejects_malformed_records() -> None:
    # 1. Missing messages
    bad_rec1 = {"data": []}
    assert validate_single_record(bad_rec1)[0] is False

    # 2. Invalid role
    bad_rec2 = {"messages": [{"role": "admin", "content": "hello"}]}
    assert validate_single_record(bad_rec2)[0] is False

    # 3. Assistant content not JSON
    bad_rec3 = {
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "user"},
            {"role": "assistant", "content": "Not JSON!"},
        ]
    }
    assert validate_single_record(bad_rec3)[0] is False

    # 4. Mutual exclusivity violation (both sql_query and error set)
    bad_rec4 = {
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "user"},
            {
                "role": "assistant",
                "content": json.dumps({
                    "thought_process": "both",
                    "tables_used": [],
                    "metrics_applied": [],
                    "sql_query": "SELECT 1",
                    "suggested_visualization": "table",
                    "error": "Some error",
                }),
            },
        ]
    }
    assert validate_single_record(bad_rec4)[0] is False

    # 5. Table mismatch between SQL AST and tables_used
    bad_rec5 = {
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "user"},
            {
                "role": "assistant",
                "content": json.dumps({
                    "thought_process": "mismatch",
                    "tables_used": ["voucher"],
                    "metrics_applied": [],
                    "sql_query": "SELECT * FROM expensive",
                    "suggested_visualization": "table",
                    "error": None,
                }),
            },
        ]
    }
    is_valid, err = validate_single_record(bad_rec5)
    assert is_valid is False
    assert "Table mismatch" in (err or "")
