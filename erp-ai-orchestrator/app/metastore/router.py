"""Sub-Schema Vector & BM25 Router for enterprise ERP schemas (>50 tables).

Features:
- Pure-Python Okapi BM25 lexical retriever (zero external C-dependencies).
- Dense semantic vector retrieval with BAAI/bge-small-en-v1.5 and graceful pure-Python fallback.
- Reciprocal Rank Fusion (RRF, k=60) combining sparse lexical and dense semantic rankings.
- Foreign-Key Graph Expansion (BFS bridging <= 2 hops) ensuring join completeness.
- Calculated metric dependency expansion.
- Metastore pruning returning a valid, scoped MetastoreSchema dict.
- Automatic bypass for schemas <= 50 tables.
"""

from __future__ import annotations

import collections
import copy
import logging
import math
import re
import time
from typing import Any, Iterable, Optional

from app.config import Settings, get_settings
from app.metastore.loader import MODULE_KEYWORDS, prune_metastore

logger = logging.getLogger(__name__)

# Tokenization regex for lexical retrieval
WORD_RE = re.compile(r"[a-zA-Z0-9_]+")


def tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase alphanumeric and snake_case tokens."""
    if not text:
        return []
    return [t.lower() for t in WORD_RE.findall(text)]


class BM25Okapi:
    """Pure-Python implementation of the Okapi BM25 ranking algorithm."""

    def __init__(self, corpus: dict[str, list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        """Initialize BM25 index.

        Args:
            corpus: Dict mapping doc_id (table name) to tokenized document terms.
            k1: BM25 k1 parameter (term frequency saturation).
            b: BM25 b parameter (document length normalization).
        """
        self.corpus = corpus
        self.k1 = k1
        self.b = b
        self.doc_ids = list(corpus.keys())
        self.n_docs = len(self.doc_ids)

        self.doc_lengths: dict[str, int] = {doc_id: len(terms) for doc_id, terms in corpus.items()}
        self.avg_doc_len = sum(self.doc_lengths.values()) / max(self.n_docs, 1)

        # Document frequencies (number of documents containing term t)
        self.doc_freqs: dict[str, int] = collections.defaultdict(int)
        # Term frequencies per document {doc_id: {term: count}}
        self.term_freqs: dict[str, dict[str, int]] = {}

        for doc_id, terms in corpus.items():
            tfs: dict[str, int] = collections.defaultdict(int)
            for t in terms:
                tfs[t] += 1
            self.term_freqs[doc_id] = tfs
            for t in tfs:
                self.doc_freqs[t] += 1

        # Precompute IDF for all observed terms
        self.idf: dict[str, float] = {}
        for term, df in self.doc_freqs.items():
            # Standard Lucene/BM25 IDF formula: ln(1 + (N - df + 0.5) / (df + 0.5))
            self.idf[term] = math.log(1.0 + (self.n_docs - df + 0.5) / (df + 0.5))

    def score(self, query_terms: list[str]) -> list[tuple[str, float]]:
        """Score all documents against query terms and return (doc_id, score) sorted desc."""
        scores: list[tuple[str, float]] = []
        for doc_id in self.doc_ids:
            doc_score = 0.0
            doc_len = self.doc_lengths[doc_id]
            len_norm = 1.0 - self.b + self.b * (doc_len / max(self.avg_doc_len, 1e-9))
            tfs = self.term_freqs[doc_id]

            for term in query_terms:
                if term not in tfs:
                    continue
                tf = tfs[term]
                idf = self.idf.get(term, 0.0)
                numerator = tf * (self.k1 + 1.0)
                denominator = tf + self.k1 * len_norm
                doc_score += idf * (numerator / denominator)

            scores.append((doc_id, doc_score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores


class PurePythonDenseRetriever:
    """High-fidelity sub-word and TF-IDF semantic vectorizer for zero-dependency runtime."""

    def __init__(self, corpus_texts: dict[str, str]) -> None:
        self.doc_ids = list(corpus_texts.keys())
        self.n_docs = len(self.doc_ids)

        # Build n-gram + word vocabulary
        self.doc_vectors: dict[str, dict[str, float]] = {}
        df: dict[str, int] = collections.defaultdict(int)

        raw_doc_features: dict[str, dict[str, float]] = {}
        for doc_id, text in corpus_texts.items():
            feats = self._extract_features(text)
            raw_doc_features[doc_id] = feats
            for f in feats:
                df[f] += 1

        self.idf: dict[str, float] = {}
        for f, count in df.items():
            self.idf[f] = math.log((self.n_docs + 1.0) / (count + 1.0)) + 1.0

        for doc_id, feats in raw_doc_features.items():
            vec: dict[str, float] = {}
            norm_sq = 0.0
            for f, tf in feats.items():
                val = (1.0 + math.log(tf)) * self.idf.get(f, 1.0)
                vec[f] = val
                norm_sq += val * val
            norm = math.sqrt(norm_sq) if norm_sq > 0 else 1.0
            self.doc_vectors[doc_id] = {k: v / norm for k, v in vec.items()}

    def _extract_features(self, text: str) -> dict[str, int]:
        feats: dict[str, int] = collections.defaultdict(int)
        tokens = tokenize(text)
        for t in tokens:
            feats[t] += 2
            # 3-char subword n-grams for typo-tolerant / semantic stem matching
            if len(t) >= 4:
                for i in range(len(t) - 2):
                    feats[t[i : i + 3]] += 1
        return feats

    def score(self, query: str) -> list[tuple[str, float]]:
        q_feats = self._extract_features(query)
        if not q_feats:
            return [(doc_id, 0.0) for doc_id in self.doc_ids]

        q_vec: dict[str, float] = {}
        norm_sq = 0.0
        for f, tf in q_feats.items():
            val = (1.0 + math.log(tf)) * self.idf.get(f, 1.0)
            q_vec[f] = val
            norm_sq += val * val
        norm = math.sqrt(norm_sq) if norm_sq > 0 else 1.0
        norm_q_vec = {k: v / norm for k, v in q_vec.items()}

        scores: list[tuple[str, float]] = []
        for doc_id in self.doc_ids:
            doc_vec = self.doc_vectors[doc_id]
            dot_product = sum(weight * doc_vec.get(feat, 0.0) for feat, weight in norm_q_vec.items())
            scores.append((doc_id, dot_product))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores


class SubSchemaRouter:
    """Hybrid BM25 + Dense Semantic Router with FK graph expansion and pruning."""

    def __init__(
        self,
        metastore: dict[str, Any],
        settings: Settings | None = None,
        force_pure_python: bool = False,
    ) -> None:
        self.metastore = metastore
        self.settings = settings or get_settings()
        self.tables = metastore.get("tables", {})
        self.allowed_joins = metastore.get("allowed_joins", [])
        self.calculated_metrics = metastore.get("calculated_metrics", {})
        self.force_pure_python = force_pure_python

        # Build table documents
        self.table_documents = self._build_table_documents()
        tokenized_corpus = {t: tokenize(doc) for t, doc in self.table_documents.items()}

        # Initialize BM25
        self.bm25 = BM25Okapi(tokenized_corpus)

        # Initialize Dense Retriever
        self.dense_retriever = self._init_dense_retriever()

        # Build undirected FK graph for bridging
        self.fk_graph = self._build_fk_graph()

    def _build_table_documents(self) -> dict[str, str]:
        """Construct comprehensive textual representation for each table."""
        docs: dict[str, str] = {}
        for table_name, table_meta in self.tables.items():
            parts: list[str] = [
                f"table {table_name}",
                f"{table_name}",
                f"{table_name.replace('_', ' ')}",
            ]
            desc = table_meta.get("description")
            if desc:
                parts.append(f"description: {desc}")

            scopes = table_meta.get("module_scope", [])
            if scopes:
                parts.append(f"module: {' '.join(scopes)}")
                for s in scopes:
                    if s in MODULE_KEYWORDS:
                        parts.append(f"keywords: {' '.join(MODULE_KEYWORDS[s])}")

            # Columns and descriptions
            cols = table_meta.get("columns", {})
            col_names: list[str] = []
            col_descs: list[str] = []
            for col_name, col_meta in cols.items():
                col_names.append(col_name)
                col_names.append(col_name.replace("_", " "))
                c_desc = col_meta.get("description")
                if c_desc:
                    col_descs.append(c_desc)
                if col_meta.get("enum_values"):
                    col_descs.append(" ".join(str(v) for v in col_meta["enum_values"]))
                if col_meta.get("known_values"):
                    col_descs.append(" ".join(str(v) for v in col_meta["known_values"]))

            parts.append(f"columns: {' '.join(col_names)}")
            if col_descs:
                parts.append(f"details: {' '.join(col_descs)}")

            # Referenced metrics
            related_metrics = [
                m_name
                for m_name, m_meta in self.calculated_metrics.items()
                if table_name in m_meta.get("required_tables", [])
            ]
            if related_metrics:
                parts.append(f"metrics: {' '.join(related_metrics)}")

            docs[table_name] = " \n ".join(parts)

        return docs

    def _init_dense_retriever(self) -> Any:
        """Initialize sentence-transformers dense embedder or pure-python fallback."""
        if not self.force_pure_python:
            try:
                from sentence_transformers import SentenceTransformer  # type: ignore

                model_name = getattr(self.settings, "schema_router_embedding_model", "BAAI/bge-small-en-v1.5")
                model = SentenceTransformer(model_name)

                # Precompute table embeddings
                table_names = list(self.table_documents.keys())
                texts = [self.table_documents[t] for t in table_names]
                embeddings = model.encode(texts, normalize_embeddings=True)

                class HFEmbeddingWrapper:
                    def __init__(self, model: Any, table_names: list[str], embeddings: Any):
                        self.model = model
                        self.table_names = table_names
                        self.embeddings = embeddings

                    def score(self, query: str) -> list[tuple[str, float]]:
                        import numpy as np  # type: ignore

                        q_emb = self.model.encode([query], normalize_embeddings=True)[0]
                        sims = np.dot(self.embeddings, q_emb)
                        res = [(self.table_names[i], float(sims[i])) for i in range(len(self.table_names))]
                        res.sort(key=lambda x: x[1], reverse=True)
                        return res

                return HFEmbeddingWrapper(model, table_names, embeddings)
            except Exception as exc:
                logger.debug("Dense neural embedding unavailable, using pure-Python vectorizer: %s", exc)

        return PurePythonDenseRetriever(self.table_documents)

    def _build_fk_graph(self) -> dict[str, set[str]]:
        """Construct undirected adjacency list from allowed joins."""
        graph: dict[str, set[str]] = collections.defaultdict(set)
        for t in self.tables:
            graph[t] = set()

        for join in self.allowed_joins:
            left = join.get("left_table")
            right = join.get("right_table")
            if left in self.tables and right in self.tables:
                graph[left].add(right)
                graph[right].add(left)

        return graph

    def search_bm25(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        """BM25 search over table documents."""
        tokens = tokenize(query)
        if not tokens:
            return [(t, 0.0) for t in list(self.tables.keys())[:top_k]]
        return self.bm25.score(tokens)[:top_k]

    def search_dense(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        """Dense semantic search over table documents."""
        return self.dense_retriever.score(query)[:top_k]

    def fuse_ranks(
        self,
        bm25_results: list[tuple[str, float]],
        dense_results: list[tuple[str, float]],
        k: int = 60,
        bm25_weight: float = 1.0,
        dense_weight: float = 1.0,
    ) -> list[tuple[str, float]]:
        """Reciprocal Rank Fusion (RRF) combining BM25 and dense rankings.

        RRF score = sum(weight / (k + rank))
        """
        rrf_scores: dict[str, float] = collections.defaultdict(float)

        for rank, (table, _) in enumerate(bm25_results, start=1):
            rrf_scores[table] += bm25_weight / (k + rank)

        for rank, (table, _) in enumerate(dense_results, start=1):
            rrf_scores[table] += dense_weight / (k + rank)

        ranked = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        return ranked

    def find_shortest_path(self, start: str, target: str) -> list[str] | None:
        """Find shortest path between two tables using BFS."""
        if start == target:
            return [start]
        if start not in self.fk_graph or target not in self.fk_graph:
            return None

        visited = {start}
        queue = collections.deque([(start, [start])])

        while queue:
            current, path = queue.popleft()
            for neighbor in self.fk_graph.get(current, set()):
                if neighbor == target:
                    return path + [neighbor]
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, path + [neighbor]))
        return None

    def expand_fk_graph(
        self,
        selected_tables: Iterable[str],
        query: str = "",
        max_tables: int = 12,
        always_include: Iterable[str] = ("accounts",),
    ) -> set[str]:
        """Expand retrieved tables with bridge tables (<=2 hops) and metric dependencies."""
        current_set = set(selected_tables) & set(self.tables.keys())

        # Always include base anchor tables if present in metastore
        for t in always_include:
            if t in self.tables:
                current_set.add(t)

        # Bridge expansion for all pairs in current_set (<= 2 hops: path length <= 3)
        tables_list = list(current_set)
        for i in range(len(tables_list)):
            for j in range(i + 1, len(tables_list)):
                u, v = tables_list[i], tables_list[j]
                path = self.find_shortest_path(u, v)
                if path and len(path) == 3:  # u -> bridge -> v (distance 2, exactly 1 bridge)
                    bridge_table = path[1]
                    if len(current_set) < max_tables:
                        current_set.add(bridge_table)

        # Metric dependency expansion
        q_lower = query.lower()
        for metric_name, metric_meta in self.calculated_metrics.items():
            req_tables = set(metric_meta.get("required_tables", []))
            # If user explicitly queried metric or multiple req_tables are already selected
            metric_tokens = set(tokenize(metric_name.replace("_", " ")))
            query_tokens = set(tokenize(q_lower))
            if metric_tokens & query_tokens or (len(req_tables & current_set) >= 2):
                if len(current_set | req_tables) <= max_tables + 2:
                    current_set |= (req_tables & set(self.tables.keys()))

        return current_set

    def route_and_prune(
        self,
        query: str,
        metastore: dict[str, Any] | None = None,
        force_prune: bool = False,
        top_k: int | None = None,
        max_k: int | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Main routing pipeline: Retrieve -> RRF -> Graph Expansion -> Prune Metastore.

        If schema has <= threshold tables and force_prune is False, returns untouched metastore.
        """
        start_time = time.perf_counter()
        target_metastore = metastore or self.metastore
        tables = target_metastore.get("tables", {})
        total_tables = len(tables)

        threshold = getattr(self.settings, "schema_router_threshold", 50)
        eff_top_k = top_k or getattr(self.settings, "schema_router_top_k", 6)
        eff_max_k = max_k or getattr(self.settings, "schema_router_max_k", 12)

        # Threshold bypass check
        if total_tables <= threshold and not force_prune:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            metadata = {
                "bypassed": True,
                "reason": f"Table count {total_tables} <= threshold {threshold}",
                "original_table_count": total_tables,
                "pruned_table_count": total_tables,
                "latency_ms": latency_ms,
                "selected_tables": sorted(tables.keys()),
            }
            return copy.deepcopy(target_metastore), metadata

        # Execute hybrid retrieval
        bm25_ranks = self.search_bm25(query, top_k=eff_top_k * 2)
        dense_ranks = self.search_dense(query, top_k=eff_top_k * 2)
        fused = self.fuse_ranks(bm25_ranks, dense_ranks, k=60)

        seed_tables = [table for table, _ in fused[:eff_top_k]]
        expanded_tables = self.expand_fk_graph(seed_tables, query=query, max_tables=eff_max_k)

        # Prune metastore
        pruned = prune_metastore(
            target_metastore,
            tables=expanded_tables,
            always_include={"accounts"},
        )

        latency_ms = (time.perf_counter() - start_time) * 1000.0
        metadata = {
            "bypassed": False,
            "original_table_count": total_tables,
            "pruned_table_count": len(pruned.get("tables", {})),
            "seed_tables": seed_tables,
            "selected_tables": sorted(expanded_tables),
            "latency_ms": latency_ms,
            "top_bm25": [t for t, _ in bm25_ranks[:3]],
            "top_dense": [t for t, _ in dense_ranks[:3]],
        }

        return pruned, metadata
