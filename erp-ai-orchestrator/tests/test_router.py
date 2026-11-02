"""Tests for Sub-Schema Vector & BM25 Router (Feature 31).

Verifies:
- BM25 Okapi lexical retrieval.
- Pure-Python dense semantic retrieval fallback.
- Reciprocal Rank Fusion (RRF, k=60) arithmetic.
- FK Graph Expansion (breadth-first bridge search <= 2 hops).
- Metric dependency expansion.
- Metastore pruning structural integrity.
- Threshold bypass (len <= 50) and force_prune behavior.
- High-scale performance on synthetic 100-table enterprise schemas.
"""

from __future__ import annotations

import copy
import time
from typing import Any

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.inference.llm_client import LLMClient
from app.main import app
from app.metastore.loader import load_metastore, prune_metastore
from app.metastore.router import (
    BM25Okapi,
    PurePythonDenseRetriever,
    SubSchemaRouter,
    tokenize,
)
from app.schemas.report_plan import ReportPlanSchema


@pytest.fixture
def base_metastore() -> dict[str, Any]:
    return load_metastore()


class TestBM25Okapi:
    def test_tokenization_basic(self) -> None:
        text = "voucher.VoucherID = voucherdetails.VoucherID_FK"
        tokens = tokenize(text)
        assert "voucher" in tokens
        assert "voucherid" in tokens
        assert "voucherdetails" in tokens
        assert "voucherid_fk" in tokens

    def test_bm25_scoring_exact_match(self) -> None:
        corpus = {
            "voucher": ["voucher", "invoice", "date", "total", "amount", "sales"],
            "expensive": ["expensive", "expense", "cost", "petrol", "diesel", "rent"],
            "accounts": ["accounts", "party", "retailer", "balance", "credit"],
        }
        bm25 = BM25Okapi(corpus)
        scores = bm25.score(["expensive", "petrol"])
        assert scores[0][0] == "expensive"
        assert scores[0][1] > scores[1][1]

    def test_bm25_unknown_term(self) -> None:
        corpus = {
            "table_a": ["alpha", "beta"],
            "table_b": ["gamma", "delta"],
        }
        bm25 = BM25Okapi(corpus)
        scores = bm25.score(["nonexistent", "quantum"])
        # All scores should be 0.0
        assert all(score == 0.0 for _, score in scores)


class TestPurePythonDenseRetriever:
    def test_semantic_feature_extraction_and_matching(self) -> None:
        docs = {
            "expensive": "daily expense tracking petrol diesel outgoings operational cost",
            "accounts": "party master ledger balances outstanding debt unpaid money",
            "products": "product catalogue sku items inventory unit price",
        }
        retriever = PurePythonDenseRetriever(docs)
        scores = retriever.score("show daily outgoings and diesel bills")
        assert scores[0][0] == "expensive"
        assert scores[0][1] > 0.1

        scores_debt = retriever.score("who has unpaid debt or balance")
        assert scores_debt[0][0] == "accounts"
        assert scores_debt[0][1] > 0.1


class TestSubSchemaRouter:
    def test_router_initialization(self, base_metastore: dict[str, Any]) -> None:
        router = SubSchemaRouter(base_metastore, force_pure_python=True)
        assert len(router.tables) == 20
        assert "voucher" in router.fk_graph
        assert "voucherdetails" in router.fk_graph["voucher"]

    def test_bm25_retrieval_exact_table_and_column(self, base_metastore: dict[str, Any]) -> None:
        router = SubSchemaRouter(base_metastore, force_pure_python=True)
        # Search for exact table name
        results = router.search_bm25("expensive date amount", top_k=5)
        top_tables = [t for t, _ in results]
        assert "expensive" in top_tables[:2]

        # Search for column names specific to batches
        results_batch = router.search_bm25("exp_date batch_number purchase_price", top_k=5)
        assert results_batch[0][0] == "batches"

    def test_dense_retrieval_semantic_synonyms(self, base_metastore: dict[str, Any]) -> None:
        router = SubSchemaRouter(base_metastore, force_pure_python=True)
        results = router.search_dense("petrol and travel costs", top_k=5)
        top_tables = [t for t, _ in results]
        assert "expensive" in top_tables[:3]

    def test_rrf_fusion_mathematics(self, base_metastore: dict[str, Any]) -> None:
        router = SubSchemaRouter(base_metastore, force_pure_python=True)
        bm25_res = [("voucher", 10.0), ("products", 5.0), ("accounts", 2.0)]
        dense_res = [("accounts", 0.9), ("voucher", 0.8), ("products", 0.1)]

        # RRF k=60
        # voucher: 1/(60+1) + 1/(60+2) = 1/61 + 1/62 = 0.01639 + 0.01613 = 0.03252
        # accounts: 1/(60+3) + 1/(60+1) = 1/63 + 1/61 = 0.01587 + 0.01639 = 0.03226
        # products: 1/(60+2) + 1/(60+3) = 1/62 + 1/63 = 0.01613 + 0.01587 = 0.03200
        fused = router.fuse_ranks(bm25_res, dense_res, k=60)
        assert fused[0][0] == "voucher"
        assert fused[1][0] == "accounts"
        assert fused[2][0] == "products"
        assert fused[0][1] > fused[1][1] > fused[2][1]

    def test_fk_graph_expansion_bridge_discovery(self, base_metastore: dict[str, Any]) -> None:
        router = SubSchemaRouter(base_metastore, force_pure_python=True)
        # In our schema: voucher connects to products ONLY via voucherdetails!
        # Path: voucher -> voucherdetails -> products
        path = router.find_shortest_path("voucher", "products")
        assert path == ["voucher", "voucherdetails", "products"]

        # When seed is {voucher, products}, graph expansion MUST pull in voucherdetails
        seed = {"voucher", "products"}
        expanded = router.expand_fk_graph(seed, query="", max_tables=10)
        assert "voucherdetails" in expanded
        assert "voucher" in expanded
        assert "products" in expanded
        assert "accounts" in expanded  # Always include accounts anchor

    def test_metric_expansion(self, base_metastore: dict[str, Any]) -> None:
        router = SubSchemaRouter(base_metastore, force_pure_python=True)
        # 'gross_profit' requires voucher, voucherdetails, products
        expanded = router.expand_fk_graph(
            selected_tables={"voucher"},
            query="Calculate our gross profit for Q3",
            max_tables=10,
        )
        assert "voucher" in expanded
        assert "voucherdetails" in expanded
        assert "products" in expanded

    def test_router_threshold_bypass(self, base_metastore: dict[str, Any]) -> None:
        settings = Settings(schema_router_threshold=50)
        router = SubSchemaRouter(base_metastore, settings=settings, force_pure_python=True)

        # Base metastore has 20 tables <= 50 threshold -> bypass
        pruned, meta = router.route_and_prune("Show expenses", force_prune=False)
        assert meta["bypassed"] is True
        assert len(pruned["tables"]) == 20
        assert meta["pruned_table_count"] == 20

        # With force_prune=True -> bypass is skipped
        pruned_forced, meta_forced = router.route_and_prune("Show daily expenses", force_prune=True, top_k=3, max_k=6)
        assert meta_forced["bypassed"] is False
        assert len(pruned_forced["tables"]) < 20
        assert "expensive" in pruned_forced["tables"]

    def test_metastore_pruning_integrity(self, base_metastore: dict[str, Any]) -> None:
        router = SubSchemaRouter(base_metastore, force_pure_python=True)
        pruned, meta = router.route_and_prune("petrol and travel expenses", force_prune=True, top_k=2, max_k=4)

        # Every table in pruned['tables'] must exist in base
        for t in pruned["tables"]:
            assert t in base_metastore["tables"]

        # Every join in pruned['allowed_joins'] must have both ends in pruned['tables']
        for join in pruned["allowed_joins"]:
            assert join["left_table"] in pruned["tables"]
            assert join["right_table"] in pruned["tables"]

        # Every metric in pruned['calculated_metrics'] must have all required_tables in pruned['tables']
        for metric_name, metric_meta in pruned["calculated_metrics"].items():
            assert set(metric_meta["required_tables"]).issubset(set(pruned["tables"].keys()))

    def test_synthetic_100_tables_scaling(self, base_metastore: dict[str, Any]) -> None:
        # Build synthetic 100-table metastore schema
        synthetic = copy.deepcopy(base_metastore)
        for i in range(21, 101):
            t_name = f"custom_enterprise_table_{i}"
            synthetic["tables"][t_name] = {
                "description": f"Synthetic ERP ledger module table {i} tracking entity attributes and events",
                "module_scope": ["accounting" if i % 2 == 0 else "inventory"],
                "columns": {
                    "id": {"data_type": "int", "description": "Primary key"},
                    f"col_{i}_amt": {"data_type": "decimal", "description": "Transaction amount"},
                },
                "row_count_snapshot": 1000,
            }
            if i % 3 == 0:
                synthetic["allowed_joins"].append({
                    "id": f"join_syn_{i}",
                    "left_table": t_name,
                    "left_column": "id",
                    "right_table": "accounts",
                    "right_column": "AccountID",
                    "relationship": "many_to_one",
                    "cardinality": "N:1",
                    "on": f"{t_name}.id = accounts.AccountID",
                })

        assert len(synthetic["tables"]) == 100

        # SubSchemaRouter with threshold=50
        settings = Settings(schema_router_threshold=50, schema_router_top_k=5, schema_router_max_k=10)
        router = SubSchemaRouter(synthetic, settings=settings, force_pure_python=True)

        start = time.perf_counter()
        pruned, meta = router.route_and_prune("Show company petrol expenses and travel bills", force_prune=False)
        duration_ms = (time.perf_counter() - start) * 1000.0

        assert meta["bypassed"] is False
        assert meta["original_table_count"] == 100
        assert meta["pruned_table_count"] <= 12
        assert "expensive" in pruned["tables"]
        # Must execute fast (< 50ms)
        assert duration_ms < 50.0

    def test_prune_metastore_defensive_against_missing_keys(self) -> None:
        minimal_schema = {
            "tables": {
                "voucher": {
                    "module_scope": ["sales_purchase"],
                    "columns": {"id": {"data_type": "int"}},
                }
            }
        }
        # Should not raise KeyError: 'modules', 'allowed_joins', 'calculated_metrics', etc.
        pruned = prune_metastore(minimal_schema, modules={"sales_purchase"})
        assert "voucher" in pruned["tables"]
        assert "modules" in pruned
        assert pruned["allowed_joins"] == []

    @pytest.mark.anyio
    async def test_llm_client_generate_report_plan_async_accepts_metastore(self) -> None:
        client = LLMClient()
        custom_meta = {
            "tables": {
                "custom_table": {
                    "description": "Custom table",
                    "module_scope": ["accounting"],
                    "columns": {"id": {"data_type": "int"}},
                }
            },
            "calculated_metrics": {},
        }
        with patch.object(client, "_prepare_request") as mock_prep:
            mock_prep.return_value = ("http://localhost/v1", {}, {})
            with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
                mock_resp = MagicMock()
                mock_resp.raise_for_status = MagicMock()
                mock_resp.json.return_value = {
                    "choices": [
                        {
                            "message": {
                                "content": '{"thought_process": "routed", "tables_used": ["custom_table"], "metrics_applied": [], "sql_query": "SELECT id FROM custom_table", "suggested_visualization": "table", "error": null}'
                            }
                        }
                    ]
                }
                mock_post.return_value = mock_resp
                plan = await client.generate_report_plan_async(
                    user_query="Query custom table",
                    metastore=custom_meta,
                    validate_ast=True,
                )
                assert plan.sql_query is not None
                assert "SELECT id FROM custom_table" in plan.sql_query
                # Confirm prompt was built with custom_meta
                system_prompt_arg = mock_prep.call_args[0][0]
                assert "custom_table" in system_prompt_arg

    def test_reports_api_router_wiring_passes_effective_metastore(self) -> None:
        test_client = TestClient(app)
        with patch("app.main.settings") as mock_settings:
            mock_settings.enable_schema_router = True
            mock_settings.schema_router_threshold = 1
            mock_settings.schema_router_top_k = 3
            mock_settings.schema_router_max_k = 5
            mock_settings.dev_mode = True
            mock_settings.jwt_secret = "test-secret"

            with patch.object(LLMClient, "generate_report_plan_async", new_callable=AsyncMock) as mock_gen:
                mock_gen.return_value = ReportPlanSchema(
                    thought_process="pruned",
                    tables_used=["expensive"],
                    metrics_applied=[],
                    sql_query="SELECT category, SUM(amount) AS total FROM expensive GROUP BY category",
                    suggested_visualization="bar",
                    error=None,
                )

                resp = test_client.post(
                    "/api/v1/reports/query",
                    json={"user_query": "Show petrol expense breakdown"},
                    headers={"X-Dev-Mode": "true", "X-Tenant-ID": "shree_shashwatraj_agro"},
                )

                assert resp.status_code == 200
                assert mock_gen.called
                call_kwargs = mock_gen.call_args.kwargs
                assert "metastore" in call_kwargs
                effective_meta = call_kwargs["metastore"]
                assert effective_meta is not None
                # Confirm that effective_metastore contains pruned tables
                assert "expensive" in effective_meta["tables"]

