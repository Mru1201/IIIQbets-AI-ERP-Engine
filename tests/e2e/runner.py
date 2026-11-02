#!/usr/bin/env python3
"""Standalone E2E Test Harness & Verification Runner (Feature 33).

Executes the 4-tier E2E verification suite:
- Tier 1: Feature Verification Tests (tests/e2e/tier1_feature_tests.py)
- Tier 2: Boundary & Corner Case Tests (tests/e2e/tier2_boundary_tests.py)
- Tier 3: Pairwise Combinatorial Tests (tests/e2e/tier3_pairwise_tests.py)
- Tier 4: Real-World ERP Business Scenarios (tests/e2e/tier4_realworld_tests.py)

Outputs:
- Formatted console summary table with per-tier pass/fail/duration metrics.
- Structured JSON report at `tests/e2e/e2e_report.json`.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent
E2E_DIR = Path(__file__).resolve().parent
ORCHESTRATOR_ROOT = WORKSPACE_ROOT / "erp-ai-orchestrator"
PIPELINE_ROOT = WORKSPACE_ROOT / "erp-ai-model-pipeline"
DEFAULT_REPORT_PATH = E2E_DIR / "e2e_report.json"

TIERS: list[dict[str, str]] = [
    {
        "id": "1",
        "name": "Tier 1: Feature Verification (Features 1-32)",
        "file": str(E2E_DIR / "tier1_feature_tests.py"),
    },
    {
        "id": "2",
        "name": "Tier 2: Boundary & Corner Cases",
        "file": str(E2E_DIR / "tier2_boundary_tests.py"),
    },
    {
        "id": "3",
        "name": "Tier 3: Pairwise Combinatorial Interactions",
        "file": str(E2E_DIR / "tier3_pairwise_tests.py"),
    },
    {
        "id": "4",
        "name": "Tier 4: Real-World Agro ERP Scenarios",
        "file": str(E2E_DIR / "tier4_realworld_tests.py"),
    },
]


def parse_pytest_counts(output: str) -> tuple[int, int, int]:
    """Extract (passed, failed, errors) counts from pytest terminal summary output."""
    passed = 0
    failed = 0
    errors = 0

    m_pass = re.search(r"(\d+)\s+passed", output)
    if m_pass:
        passed = int(m_pass.group(1))

    m_fail = re.search(r"(\d+)\s+failed", output)
    if m_fail:
        failed = int(m_fail.group(1))

    m_err = re.search(r"(\d+)\s+errors?", output)
    if m_err:
        errors = int(m_err.group(1))

    return passed, failed, errors


def run_tier(tier: dict[str, str], timeout_s: int = 180, verbose: bool = False) -> dict[str, Any]:
    """Execute a single E2E test tier via pytest subprocess with timeout enforcement."""
    env = os.environ.copy()
    existing_py_path = env.get("PYTHONPATH", "")
    extra_paths = f"{ORCHESTRATOR_ROOT}:{PIPELINE_ROOT}:{WORKSPACE_ROOT}"
    env["PYTHONPATH"] = f"{extra_paths}:{existing_py_path}" if existing_py_path else extra_paths

    cmd = [sys.executable, "-m", "pytest", tier["file"], "-v" if verbose else "-q", "--tb=short"]
    start_t = time.perf_counter()

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(WORKSPACE_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        duration_s = round(time.perf_counter() - start_t, 2)
        combined_out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        passed, failed, errors = parse_pytest_counts(combined_out)
        total = passed + failed + errors
        status = "PASSED" if (proc.returncode == 0 and failed == 0 and errors == 0 and passed > 0) else "FAILED"

        if verbose or status == "FAILED":
            print(combined_out)

        return {
            "tier_id": tier["id"],
            "tier": tier["name"],
            "file": str(Path(tier["file"]).relative_to(WORKSPACE_ROOT)),
            "status": status,
            "return_code": proc.returncode,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "total": total,
            "duration_s": duration_s,
        }
    except subprocess.TimeoutExpired:
        duration_s = round(time.perf_counter() - start_t, 2)
        return {
            "tier_id": tier["id"],
            "tier": tier["name"],
            "file": str(Path(tier["file"]).relative_to(WORKSPACE_ROOT)),
            "status": "TIMEOUT",
            "return_code": -1,
            "passed": 0,
            "failed": 0,
            "errors": 1,
            "total": 1,
            "duration_s": duration_s,
        }


def print_summary_table(results: list[dict[str, Any]], total_duration_s: float) -> None:
    """Print formatted ASCII summary table of E2E test execution."""
    sep = "=" * 92
    thin = "-" * 92
    print(f"\n{sep}")
    print(" AUTONOMOUS AI ERP REPORTING SYSTEM — DUAL-TRACK E2E VERIFICATION SUMMARY")
    print(sep)
    print(f" {'Tier':<46} | {'Status':<8} | {'Passed':>6} | {'Failed':>6} | {'Time (s)':>8}")
    print(thin)

    total_passed = 0
    total_failed = 0
    total_errors = 0

    for r in results:
        total_passed += r["passed"]
        total_failed += r["failed"]
        total_errors += r["errors"]
        print(
            f" {r['tier']:<46} | {r['status']:<8} | {r['passed']:>6} | "
            f"{r['failed'] + r['errors']:>6} | {r['duration_s']:>8.2f}"
        )

    print(thin)
    grand_total = total_passed + total_failed + total_errors
    overall_status = "PASSED" if (total_failed == 0 and total_errors == 0 and grand_total > 0) else "FAILED"
    pass_rate = round((total_passed / grand_total) * 100.0, 2) if grand_total > 0 else 0.0
    print(
        f" {'TOTAL (' + str(pass_rate) + '% PASS RATE)':<46} | {overall_status:<8} | "
        f"{total_passed:>6} | {total_failed + total_errors:>6} | {total_duration_s:>8.2f}"
    )
    print(f"{sep}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Standalone E2E Verification Test Runner")
    parser.add_argument(
        "--tier",
        choices=["1", "2", "3", "4", "all"],
        default="all",
        help="Target E2E tier to execute (default: all)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="Timeout in seconds per tier (default: 180)",
    )
    parser.add_argument(
        "--report",
        type=str,
        default=str(DEFAULT_REPORT_PATH),
        help="Output path for JSON telemetry report",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print verbose pytest output",
    )
    args = parser.parse_args()

    selected_tiers = TIERS if args.tier == "all" else [t for t in TIERS if t["id"] == args.tier]

    suite_start = time.perf_counter()
    results: list[dict[str, Any]] = []
    for tier in selected_tiers:
        res = run_tier(tier, timeout_s=args.timeout, verbose=args.verbose)
        results.append(res)

    total_duration_s = round(time.perf_counter() - suite_start, 2)
    print_summary_table(results, total_duration_s)

    total_passed = sum(r["passed"] for r in results)
    total_failed = sum(r["failed"] for r in results)
    total_errors = sum(r["errors"] for r in results)
    grand_total = total_passed + total_failed + total_errors
    overall_status = "PASSED" if (total_failed == 0 and total_errors == 0 and grand_total > 0) else "FAILED"

    report_payload = {
        "suite": "Autonomous AI ERP Reporting System - E2E Verification Suite",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": overall_status,
        "total_tests": grand_total,
        "passed": total_passed,
        "failed": total_failed,
        "errors": total_errors,
        "pass_rate_pct": round((total_passed / grand_total) * 100.0, 2) if grand_total > 0 else 0.0,
        "total_duration_s": total_duration_s,
        "tiers": results,
    }

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report_payload, indent=2), encoding="utf-8")
    print(f"JSON E2E report written to: {report_path}")

    return 0 if overall_status == "PASSED" else 1


if __name__ == "__main__":
    sys.exit(main())
