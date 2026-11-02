# E2E Test Infrastructure & Verification Specification (`TEST_INFRA.md`)

## 1. Overview & Architecture
The Autonomous AI ERP Reporting System uses a **Dual-Track Opaque-Box & Full-Stack E2E Test Architecture** (`tests/e2e/`) designed to validate all 35 system features across the three core subsystems and host ERP application:
1. **`erp-ai-orchestrator`**: FastAPI server, JWT/DEV_MODE auth, Universal Multi-Provider LLM Client, Metastore Loader & Sub-Schema Router (`BM25` + `Dense` + `RRF` + `FK BFS`), SQLGlot AST Security Guardrail, 2-Step Self-Correction State Machine, Dual-Mode Database Execution Engine (MySQL Pool + SQLite Auto-Fallback from `ledger (34).sql`), Dynamic UI Schema Deriver, and 200-Query Gold Evaluation Benchmark Suite.
2. **`erp-ai-model-pipeline`**: 3,000-sample hybrid ChatML dataset builder (`train.jsonl` / `val.jsonl`), Unsloth QLoRA training pipeline (`--dry-run` and GPU execution modes), and Dual Format Exporter (vLLM 16-bit `safetensors` + Ollama `Q4_K_M` `GGUF` & `Modelfile`).
3. **`erp-ai-workspace-ui` & `shreeshashwatraj-frontend`**: SAP Fiori 3-Zone Analytical List Page (ALP) React workspace, Zone 1 KPI Header & Filter Bar, Zone 2 TanStack High-Density Table, Interactive Charts & SheetJS Export Engine, Zone 3 Joule Copilot Drawer & NetSuite `fx` Formula Inspector, `Cmd+K` Awesomebar, `<erp-ai-reporter>` Shadow DOM Web Component bundle (`dist/wc/erp-ai-reporter.umd.js` & `.es.js`), and host ERP routes (`/ai-reports` and `Reports.js`).

```
┌──────────────────────────────────────────────────────────────────────────┐
│                     Standalone E2E Runner (tests/e2e/runner.py)          │
├─────────────────┬─────────────────┬──────────────────┬───────────────────┤
│  Tier 1         │  Tier 2         │  Tier 3          │  Tier 4           │
│  Feature Tests  │  Boundary Tests │  Pairwise Tests  │  Real-World Tests │
│  (Features 1-32)│  (Corner Cases) │  (Combinatorial) │  (Agro ERP Flows) │
└────────┬────────┴────────┬────────┴─────────┬────────┴─────────┬─────────┘
         │                 │                  │                  │
         ▼                 ▼                  ▼                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  System Under Test (FastAPI TestClient + SQLite Seed DB + Artifacts)     │
│  • ledger (34).sql (23 tables, 284 seed rows, 8 canonical metrics)       │
│  • metastore_schema.json (20 whitelisted tables, 2 restricted tables)    │
│  • erp-ai-model-pipeline (3,000 JSONL samples, safetensors, GGUF)        │
│  • erp-ai-workspace-ui & shreeshashwatraj-frontend (Bundles & Mounts)    │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Four-Tier Verification Structure

| Tier | File | Focus | Target Count | Description |
|------|------|-------|--------------|-------------|
| **Tier 1** | `tests/e2e/tier1_feature_tests.py` | Feature Verification | ≥160 tests (≥5 per feature across Features 1–32) | Exhaustive verification of every feature contract, API schema, SQL execution path, model pipeline artifact, UI component, and host mount. |
| **Tier 2** | `tests/e2e/tier2_boundary_tests.py` | Boundary & Corner Cases | ≥60 tests | Empty/whitespace queries, `LIMIT` boundary caps (`1`, `1000`, `10000`), SQLi/AST attack vectors, malformed/expired JWTs, oversized schemas, zero-row results, and `NULL`/`NaN`/`Infinity`/`Decimal` normalization. |
| **Tier 3** | `tests/e2e/tier3_pairwise_tests.py` | Combinatorial Interactions | ≥60 tests | Pairwise combinations across module scopes (`sales`, `finance`, `inventory`, `tax`, `orders`, `all`), auth modes (`JWT` vs `DEV_MODE`), visualization types (`kpi_grid`, `bar_chart`, `line_chart`, `donut_chart`, `table`), and self-correction retry trajectories (`0`, `1`, `2` retries + exhaustion). |
| **Tier 4** | `tests/e2e/tier4_realworld_tests.py` | Real-World Business Scenarios | ≥40 tests | End-to-end Shree Shashwatraj Agro Pvt Ltd workflows: P&L waterfall reconciliation (`10,368` Sales - `7,900` COGS = `2,468` Gross Profit - `45,700` Expenses = `-43,232` Net Profit), Kacha (`stock transfer`) vs Pakka (`Sales`) isolation, sundry debtor unpaid balance aging (`110,949.60`), live batch stock valuation (`7,044,779.00`), GST tax inclusive reporting, and multi-hop schema routing. |

---

## 3. Running the E2E Suite

### CLI Test Harness (`tests/e2e/runner.py`)
Execute the full 4-tier E2E suite from the workspace root using the orchestrator virtual environment:

```bash
erp-ai-orchestrator/.venv/bin/python tests/e2e/runner.py
```

#### Optional CLI Arguments
- `--tier {1,2,3,4,all}`: Run a specific tier or all tiers (default: `all`).
- `--timeout <seconds>`: Per-tier execution timeout in seconds (default: `180`).
- `--report <path>`: Path to output JSON report (default: `tests/e2e/e2e_report.json`).
- `--verbose` / `-v`: Enable verbose pytest output.

### Direct Pytest Execution
```bash
PYTHONPATH="erp-ai-orchestrator:erp-ai-model-pipeline" \
  erp-ai-orchestrator/.venv/bin/python -m pytest tests/e2e -v
```

### Subsystem Test Suites
```bash
# 1. Orchestrator Unit & Adversarial Suite (404+ tests)
erp-ai-orchestrator/.venv/bin/python -m pytest erp-ai-orchestrator/tests -q

# 2. Model Pipeline Unit Suite (10+ tests)
PYTHONPATH="erp-ai-model-pipeline:erp-ai-orchestrator" \
  erp-ai-orchestrator/.venv/bin/python -m pytest erp-ai-model-pipeline/tests -q

# 3. Workspace UI Vitest Suite (82+ tests)
npm --prefix erp-ai-workspace-ui test -- --run
```

---

## 4. JSON Telemetry Report (`tests/e2e/e2e_report.json`)
`runner.py` produces a machine-readable JSON report with the following schema:
```json
{
  "suite": "Autonomous AI ERP Reporting System - E2E Verification Suite",
  "status": "PASSED",
  "total_tests": 330,
  "passed": 330,
  "failed": 0,
  "errors": 0,
  "pass_rate_pct": 100.0,
  "total_duration_s": 4.25,
  "tiers": [
    {
      "tier": "Tier 1: Feature Tests",
      "file": "tests/e2e/tier1_feature_tests.py",
      "status": "PASSED",
      "passed": 165,
      "failed": 0,
      "errors": 0,
      "total": 165,
      "duration_s": 2.10
    }
  ]
}
```
