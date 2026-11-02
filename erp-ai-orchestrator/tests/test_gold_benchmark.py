"""Unit tests for Gold Evaluation Benchmark Suite (Feature 32)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.gold_eval_suite import (
    DEFAULT_BENCHMARK_PATH,
    GoldBenchmarkSample,
    GoldEvalHarness,
    compare_results,
    normalize_value,
)


def test_gold_queries_json_schema_and_distribution() -> None:
    assert DEFAULT_BENCHMARK_PATH.exists(), f"Missing {DEFAULT_BENCHMARK_PATH}"
    with open(DEFAULT_BENCHMARK_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert len(data) == 200, f"Expected exactly 200 queries, found {len(data)}"

    # Parse every sample
    samples = [GoldBenchmarkSample.model_validate(item) for item in data]
    ids = [s.id for s in samples]
    assert len(set(ids)) == 200, "Duplicate sample IDs found"

    # Verify ID sequence
    for i in range(1, 201):
        expected_id = f"gold_{i:03d}"
        assert ids[i - 1] == expected_id

    # Verify distribution
    cats = [s.category for s in samples]
    assert cats.count("single_table") == 40
    assert cats.count("two_table_join") == 40
    assert cats.count("complex_aggregation") == 45
    assert cats.count("time_series_date") == 45
    assert cats.count("adversarial_security") == 30

    # Verify adversarial properties
    for s in samples:
        if s.category == "adversarial_security":
            assert s.is_adversarial is True
            assert s.expected_intent == "REFUSAL"
            assert s.expected_error_code is not None
        else:
            assert s.is_adversarial is False
            assert s.expected_intent == "SELECT"
            assert s.ground_truth_sql is not None


def test_gold_eval_suite_gt_verify() -> None:
    harness = GoldEvalHarness(benchmark_file=DEFAULT_BENCHMARK_PATH)
    summary = harness.run_gt_verification()

    assert summary.total_samples == 200
    assert summary.passed_samples == 200
    assert summary.failed_samples == 0
    assert summary.execution_accuracy == 100.0
    assert summary.syntax_validity_rate == 100.0
    assert summary.guardrail_compliance_rate == 100.0
    assert summary.latency_p50_ms > 0.0
    assert summary.latency_p95_ms > 0.0


def test_gold_eval_suite_mock_mode(tmp_path: Path) -> None:
    harness = GoldEvalHarness(benchmark_file=DEFAULT_BENCHMARK_PATH)
    summary = harness.run_mock_evaluation()

    report_file = tmp_path / "mock_report.json"
    report_file.write_text(summary.model_dump_json(indent=2))

    assert report_file.exists()
    loaded = json.loads(report_file.read_text())
    assert loaded["total_samples"] == 200
    assert loaded["execution_accuracy"] >= 88.0


def test_compare_results_float_tolerance() -> None:
    # Exact match
    r1 = [{"total": 10368.0, "cogs": 7900.0}]
    r2 = [{"total": 10368.0, "cogs": 7900.0}]
    assert compare_results(r1, r2, tolerance=0.01) is True

    # Float variation within epsilon
    r3 = [{"total": 10368.004, "cogs": 7899.998}]
    assert compare_results(r1, r3, tolerance=0.01) is True

    # Float variation outside epsilon
    r4 = [{"total": 10368.15, "cogs": 7900.0}]
    assert compare_results(r1, r4, tolerance=0.01) is False


def test_compare_results_case_insensitivity_and_reordering() -> None:
    # Keys differ in casing, rows differ in order
    gen = [
        {"Product": "D-K-60", "Units": 5},
        {"Product": "SARVOTTAM", "Units": 2},
    ]
    gt = [
        {"product": "sarvottam", "units": 2.0},
        {"product": "d-k-60", "units": 5.0},
    ]
    assert compare_results(gen, gt, tolerance=0.01) is True

    # Mismatched length
    gen_short = [{"Product": "D-K-60", "Units": 5}]
    assert compare_results(gen_short, gt) is False


def test_normalize_value() -> None:
    assert normalize_value(None) is None
    assert normalize_value(10) == 10.0
    assert normalize_value(" 10.50 ") == 10.50
    assert normalize_value(" Sales ") == "sales"
