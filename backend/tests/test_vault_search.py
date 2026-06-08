"""Unit tests for src.community.knowledge_vault_search."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from src.community.knowledge_vault_search import vector_index as vi
from src.community.knowledge_vault_search.search import (
    VALID_CATEGORIES,
    VaultSearcher,
    _bm25_score,
    _excerpt,
    _tokenize,
)
from src.community.knowledge_vault_search.vector_index import VaultVectorIndex

# ---------------------------------------------------------------------------
# Pure-function tests (no disk I/O)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _disable_hybrid_search_for_lexical_tests(monkeypatch):
    monkeypatch.setattr(
        "src.control_plane.services.unified_vault_search.get_app_config",
        lambda: SimpleNamespace(
            knowledge_vault=SimpleNamespace(
                vector_search_enabled=False,
                hybrid_rrf_k=60,
                vector_dimensions=256,
                vector_chunk_chars=1200,
                vector_chunk_overlap_chars=200,
                vector_backend="openai_compatible",
                vector_embedding_model="",
            )
        ),
    )


class TestTokenize:
    def test_basic(self):
        assert _tokenize("Hello World") == ["hello", "world"]

    def test_numbers_included(self):
        assert "2024" in _tokenize("report 2024")

    def test_punctuation_stripped(self):
        tokens = _tokenize("foo, bar! baz.")
        assert tokens == ["foo", "bar", "baz"]

    def test_empty_string(self):
        assert _tokenize("") == []

    def test_lowercased(self):
        assert _tokenize("LangGraph") == ["langgraph"]


class TestBM25Score:
    def test_zero_for_no_match(self):
        score = _bm25_score(["python"], ["java", "kotlin"], avg_dl=2.0)
        assert score == 0.0

    def test_positive_for_match(self):
        score = _bm25_score(["python"], ["python", "is", "great"], avg_dl=3.0)
        assert score > 0.0

    def test_higher_freq_scores_higher(self):
        low = _bm25_score(["cat"], ["cat", "dog", "bird"], avg_dl=3.0)
        high = _bm25_score(["cat"], ["cat", "cat", "cat"], avg_dl=3.0)
        assert high > low

    def test_empty_query_returns_zero(self):
        assert _bm25_score([], ["cat", "dog"], avg_dl=2.0) == 0.0

    def test_empty_doc_returns_zero(self):
        assert _bm25_score(["cat"], [], avg_dl=2.0) == 0.0


class TestExcerpt:
    def test_returns_string(self):
        result = _excerpt("The quick brown fox jumps over the lazy dog", ["fox"])
        assert isinstance(result, str)

    def test_contains_context_around_match(self):
        body = "nothing here. The target word appears in the middle. nothing here."
        result = _excerpt(body, ["target"])
        assert "target" in result

    def test_falls_back_to_beginning_when_no_match(self):
        result = _excerpt("hello world this is a test", ["zzznomatch"])
        assert result.startswith("hello")

    def test_max_length(self):
        long_body = "word " * 1000
        result = _excerpt(long_body, ["word"])
        assert len(result) <= 400


# ---------------------------------------------------------------------------
# VaultSearcher tests (use tmp_path for fake vault)
# ---------------------------------------------------------------------------


def _make_vault(tmp_path: Path) -> Path:
    """Create a minimal vault structure under tmp_path and return the root."""
    vault = tmp_path / "knowledge_vault"
    compiled = vault / "02_compiled"
    for cat in VALID_CATEGORIES:
        (compiled / cat).mkdir(parents=True, exist_ok=True)
    return vault


def _write_page(vault: Path, category: str, filename: str, title: str, body: str, tags: list[str] | None = None) -> Path:
    tags_str = json.dumps([str(t) for t in (tags or [])])
    content = f"---\ntitle: {json.dumps(title)}\ntags: {tags_str}\n---\n\n{body}"
    path = vault / "02_compiled" / category / filename
    path.write_text(content, encoding="utf-8")
    return path


class TestVaultSearcherEmptyVault:
    def test_returns_empty_list_when_no_pages(self, tmp_path):
        vault = _make_vault(tmp_path)
        searcher = VaultSearcher(vault)
        assert searcher.search("anything") == []

    def test_returns_empty_list_when_compiled_dir_missing(self, tmp_path):
        vault = tmp_path / "knowledge_vault"
        vault.mkdir(parents=True)
        searcher = VaultSearcher(vault)
        assert searcher.search("anything") == []


class TestVaultSearcherBasic:
    def test_finds_matching_page(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_page(vault, "sources", "langgraph.md", "LangGraph Overview", "LangGraph is a graph-based agent framework.")
        searcher = VaultSearcher(vault)
        results = searcher.search("LangGraph agent")
        assert len(results) == 1
        assert results[0]["title"] == "LangGraph Overview"

    def test_returns_no_results_for_zero_score(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_page(vault, "sources", "unrelated.md", "Cooking Tips", "Boil pasta until al dente.")
        searcher = VaultSearcher(vault)
        results = searcher.search("quantum physics semiconductor")
        assert results == []

    def test_result_fields_present(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_page(vault, "concepts", "memory.md", "Memory Systems", "Memory is important for agents.")
        searcher = VaultSearcher(vault)
        results = searcher.search("memory agents")
        assert len(results) == 1
        r = results[0]
        for key in ("title", "category", "score", "excerpt", "tags", "source_url", "path"):
            assert key in r, f"Missing field: {key}"

    def test_category_field_correct(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_page(vault, "syntheses", "ai_synthesis.md", "AI Research Synthesis", "AI is transforming many industries.")
        searcher = VaultSearcher(vault)
        results = searcher.search("AI industries")
        assert results[0]["category"] == "syntheses"

    def test_score_is_positive(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_page(vault, "sources", "doc.md", "Test Doc", "Python is a great programming language.")
        searcher = VaultSearcher(vault)
        results = searcher.search("Python programming")
        assert results[0]["score"] > 0


class TestVaultSearcherRanking:
    def test_more_relevant_page_ranks_higher(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_page(vault, "sources", "high.md", "High Relevance", "Python Python Python is the best language for data science.")
        _write_page(vault, "sources", "low.md", "Low Relevance", "Java is also a popular language for enterprise software.")
        searcher = VaultSearcher(vault)
        results = searcher.search("Python")
        assert results[0]["title"] == "High Relevance"

    def test_results_sorted_descending_by_score(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_page(vault, "sources", "a.md", "A", "machine learning machine learning machine learning deep learning")
        _write_page(vault, "sources", "b.md", "B", "machine learning introduction")
        _write_page(vault, "sources", "c.md", "C", "unrelated content about cooking and food")
        searcher = VaultSearcher(vault)
        results = searcher.search("machine learning")
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)


class TestVaultSearcherLimit:
    def test_respects_limit(self, tmp_path):
        vault = _make_vault(tmp_path)
        for i in range(10):
            _write_page(vault, "sources", f"doc{i}.md", f"Doc {i}", f"neural network deep learning model {i}")
        searcher = VaultSearcher(vault)
        results = searcher.search("neural network deep learning", limit=3)
        assert len(results) <= 3

    def test_limit_capped_at_20_by_tool(self, tmp_path):
        # The tool enforces min(20, limit); VaultSearcher itself respects whatever is passed.
        vault = _make_vault(tmp_path)
        for i in range(5):
            _write_page(vault, "sources", f"doc{i}.md", f"Doc {i}", f"content about topic {i}")
        searcher = VaultSearcher(vault)
        results = searcher.search("content topic", limit=100)
        assert len(results) <= 5  # only 5 pages exist


class TestVaultSearcherCategoryFilter:
    def test_only_searches_specified_categories(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_page(vault, "sources", "src.md", "Source Page", "blockchain distributed ledger technology")
        _write_page(vault, "concepts", "con.md", "Concept Page", "blockchain consensus mechanism")
        searcher = VaultSearcher(vault)
        results = searcher.search("blockchain", categories=["concepts"])
        assert all(r["category"] == "concepts" for r in results)

    def test_ignores_invalid_categories(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_page(vault, "sources", "page.md", "Page", "content about artificial intelligence")
        searcher = VaultSearcher(vault)
        # "nonexistent" is silently dropped; "sources" is valid
        results = searcher.search("artificial intelligence", categories=["sources", "nonexistent"])
        assert len(results) == 1

    def test_all_categories_searched_by_default(self, tmp_path):
        vault = _make_vault(tmp_path)
        for cat in VALID_CATEGORIES:
            _write_page(vault, cat, f"{cat}.md", f"{cat.title()} Page", f"renewable energy solar wind {cat}")
        searcher = VaultSearcher(vault)
        results = searcher.search("renewable energy solar")
        returned_cats = {r["category"] for r in results}
        assert returned_cats == set(VALID_CATEGORIES)


class TestVaultSearcherTags:
    def test_tags_returned_in_result(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_page(vault, "entities", "openai.md", "OpenAI", "OpenAI develops GPT models.", tags=["ai", "company"])
        searcher = VaultSearcher(vault)
        results = searcher.search("OpenAI GPT")
        assert results[0]["tags"] == ["ai", "company"]

    def test_tags_boost_relevance(self, tmp_path):
        vault = _make_vault(tmp_path)
        # One page has the query term in tags (boosted via text field), another only in body
        _write_page(vault, "entities", "tagged.md", "Tagged Page", "some general content here.", tags=["transformer"])
        _write_page(vault, "entities", "body.md", "Body Only", "transformer architecture is key to modern NLP.")
        searcher = VaultSearcher(vault)
        results = searcher.search("transformer")
        # Both should appear; tagged page should score because title repeated + tag text
        titles = [r["title"] for r in results]
        assert "Tagged Page" in titles
        assert "Body Only" in titles


class _FakeEmbedder:
    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        self.batch_sizes.append(len(texts))
        return [np.array([1.0, float(index + 1), 0.5], dtype=np.float32) for index, _text in enumerate(texts)]

    def resolved_model_name(self) -> str:
        return "fake-embedding-model"


class TestVaultVectorIndexInvalidation:
    def test_stale_zero_chunk_metadata_rebuilds_when_compiled_pages_exist(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_page(vault, "sources", "langgraph.md", "LangGraph Overview", "LangGraph supports agent workflows.")

        index = VaultVectorIndex(vault)
        fake = _FakeEmbedder()
        index._embedder = fake  # noqa: SLF001
        index.metadata_path.write_text(
            json.dumps(
                {
                    "backend": "openai_compatible",
                    "effective_backend": "none",
                    "embedding_model": "",
                    "effective_embedding_model": "old-chat-model",
                    "dimensions": 256,
                    "chunk_chars": index.chunk_chars,
                    "overlap_chars": index.overlap_chars,
                    "built_at": "2026-06-05T18:32:42+00:00",
                    "chunk_count": 0,
                    "chunks": [],
                }
            ),
            encoding="utf-8",
        )

        payload = index.load()

        assert payload["chunk_count"] > 0
        assert payload["effective_backend"] == "openai_compatible"
        assert payload["effective_embedding_model"] == "fake-embedding-model"
        assert payload["compiled_signature"]["page_count"] == 1
        assert index.matrix_path.exists()

    def test_embedding_requests_are_batched(self, tmp_path):
        vault = _make_vault(tmp_path)
        for i in range(5):
            _write_page(vault, "sources", f"doc-{i}.md", f"Doc {i}", f"batch embedding content {i}")

        index = VaultVectorIndex(vault)
        fake = _FakeEmbedder()
        index._embedder = fake  # noqa: SLF001
        index._embed_batch_size = 2  # noqa: SLF001

        payload = index.build()

        assert payload["chunk_count"] == 5
        assert fake.batch_sizes == [2, 2, 1]


# ---------------------------------------------------------------------------
# Tool-level tests (test the @tool wrapper)
# ---------------------------------------------------------------------------


class TestQueryKnowledgeVaultTool:
    """Test the LangChain tool wrapper in isolation by monkey-patching _get_searcher."""

    def _invoke(self, monkeypatch, results, **kwargs):
        """Helper: patch _get_searcher and invoke the tool."""
        from src.community.knowledge_vault_search import tool as tool_module

        mock_searcher = MagicMock()
        mock_searcher.search.return_value = results
        monkeypatch.setattr(tool_module, "_get_searcher", lambda: mock_searcher)
        monkeypatch.setattr(tool_module, "_searcher", None)

        from src.community.knowledge_vault_search.tool import query_knowledge_vault_tool

        return query_knowledge_vault_tool.invoke({"query": "test query", **kwargs})

    def test_returns_json_string(self, monkeypatch, tmp_path):
        raw = self._invoke(monkeypatch, [{"title": "T", "category": "sources", "score": 1.0, "excerpt": "x", "tags": [], "source_url": "", "path": "/p"}])
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)

    def test_ok_true_with_results(self, monkeypatch, tmp_path):
        raw = self._invoke(monkeypatch, [{"title": "T", "category": "sources", "score": 1.0, "excerpt": "x", "tags": [], "source_url": "", "path": "/p"}])
        parsed = json.loads(raw)
        assert parsed["ok"] is True
        assert len(parsed["results"]) == 1

    def test_ok_true_empty_results(self, monkeypatch, tmp_path):
        raw = self._invoke(monkeypatch, [])
        parsed = json.loads(raw)
        assert parsed["ok"] is True
        assert parsed["results"] == []
        assert "message" in parsed

    def test_invalid_category_returns_error(self, monkeypatch, tmp_path):
        from src.community.knowledge_vault_search import tool as tool_module

        mock_searcher = MagicMock()
        monkeypatch.setattr(tool_module, "_get_searcher", lambda: mock_searcher)

        from src.community.knowledge_vault_search.tool import query_knowledge_vault_tool

        raw = query_knowledge_vault_tool.invoke({"query": "test", "categories": ["invalid_cat"]})
        parsed = json.loads(raw)
        assert parsed["ok"] is False
        assert parsed["error"] == "invalid_categories"

    def test_limit_clamped_to_1_minimum(self, monkeypatch):
        from src.community.knowledge_vault_search import tool as tool_module

        mock_searcher = MagicMock()
        mock_searcher.search.return_value = []
        monkeypatch.setattr(tool_module, "_get_searcher", lambda: mock_searcher)

        from src.community.knowledge_vault_search.tool import query_knowledge_vault_tool

        query_knowledge_vault_tool.invoke({"query": "test", "limit": -5})
        mock_searcher.search.assert_called_once()
        call_args = mock_searcher.search.call_args
        assert call_args.kwargs.get("limit", call_args.args[2] if len(call_args.args) > 2 else None) == 1

    def test_limit_clamped_to_20_maximum(self, monkeypatch):
        from src.community.knowledge_vault_search import tool as tool_module

        mock_searcher = MagicMock()
        mock_searcher.search.return_value = []
        monkeypatch.setattr(tool_module, "_get_searcher", lambda: mock_searcher)

        from src.community.knowledge_vault_search.tool import query_knowledge_vault_tool

        query_knowledge_vault_tool.invoke({"query": "test", "limit": 999})
        mock_searcher.search.assert_called_once()
        call_args = mock_searcher.search.call_args
        assert call_args.kwargs.get("limit", call_args.args[2] if len(call_args.args) > 2 else None) == 20

    def test_exception_returns_ok_false(self, monkeypatch):
        from src.community.knowledge_vault_search import tool as tool_module

        mock_searcher = MagicMock()
        mock_searcher.search.side_effect = RuntimeError("disk error")
        monkeypatch.setattr(tool_module, "_get_searcher", lambda: mock_searcher)

        from src.community.knowledge_vault_search.tool import query_knowledge_vault_tool

        raw = query_knowledge_vault_tool.invoke({"query": "test"})
        parsed = json.loads(raw)
        assert parsed["ok"] is False
        assert "disk error" in parsed["error"]


# ---------------------------------------------------------------------------
# VaultVectorIndex rebuild safety: no inline rebuild from search(), build lock
# collapses the thundering herd, cooldown throttles repeated attempts.
# (Embedder is stubbed so these never touch a real /embeddings endpoint.)
# ---------------------------------------------------------------------------


def _fake_vec(text: str, dims: int) -> np.ndarray:
    """Deterministic, all-positive bag-of-tokens embedding.

    All entries are >= a positive baseline, so the cosine similarity between any
    two vectors is strictly positive (matching tokens add more). This mirrors
    real embeddings (related text is positively correlated) and avoids the
    random-sign flakiness a Gaussian embedding would introduce against the
    ``score <= 0`` filter in ``VaultVectorIndex.search``.
    """
    vec = np.full(dims, 0.1, dtype=np.float32)
    for token in _tokenize(text):
        vec[int(hashlib.sha1(token.encode()).hexdigest(), 16) % dims] += 1.0
    return vec


class _CountingEmbedder:
    """Deterministic, offline embedder that counts batch calls (thread-safe)."""

    def __init__(self, dims: int = 8) -> None:
        import threading

        self.dims = dims
        self.batch_calls = 0
        self._lock = threading.Lock()

    def embed_batch(self, texts):
        with self._lock:
            self.batch_calls += 1
        return [_fake_vec(t, self.dims) for t in texts]

    def embed_one(self, text):
        return _fake_vec(text, self.dims)

    def resolved_model_name(self) -> str:
        return "fake-embedding-model"


def _make_index(vault: Path, embedder: _CountingEmbedder) -> VaultVectorIndex:
    idx = VaultVectorIndex(vault, dimensions=embedder.dims, chunk_chars=400, overlap_chars=50)
    idx._embedder = embedder
    return idx


def _poll_until_searchable(idx: VaultVectorIndex, query: str, timeout: float = 5.0) -> list:
    """Poll search() until the background rebuild lands and it returns hits.

    This polls the observable contract (search results) rather than internal
    rebuild bookkeeping, so it is free of background-thread timing races.
    """
    import time as _time

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        results = idx.search(query)
        if results:
            return results
        _time.sleep(0.02)
    return idx.search(query)


class TestVectorIndexRebuildSafety:
    def test_search_does_not_rebuild_inline(self, tmp_path):
        """A search on a missing index returns [] immediately without embedding inline.

        The async scheduler is stubbed out so this asserts purely the inline
        path: no embedding work, immediate lexical-fallback [].
        """
        vault = _make_vault(tmp_path)
        _write_page(vault, "sources", "lg.md", "LangGraph", "LangGraph is a graph agent framework.")
        embedder = _CountingEmbedder()
        idx = _make_index(vault, embedder)

        scheduled = {"count": 0}
        idx.ensure_built_async = lambda: scheduled.__setitem__("count", scheduled["count"] + 1)  # type: ignore[method-assign]

        assert idx.search("LangGraph agent") == []
        assert embedder.batch_calls == 0, "search must not embed the vault inline"
        assert scheduled["count"] == 1, "search must schedule exactly one background rebuild"

    def test_search_returns_hits_after_background_rebuild(self, tmp_path):
        """After the scheduled background rebuild lands, search returns vector hits."""
        vault = _make_vault(tmp_path)
        _write_page(vault, "sources", "lg.md", "LangGraph", "LangGraph is a graph agent framework.")
        embedder = _CountingEmbedder()
        idx = _make_index(vault, embedder)

        # First search returns [] and schedules the rebuild in the background.
        assert idx.search("LangGraph agent") == []

        # Poll the real contract: eventually a search returns hits.
        results = _poll_until_searchable(idx, "LangGraph agent")
        assert results and results[0]["page_id"] == "lg"
        assert embedder.batch_calls >= 1
        assert idx.matrix_path.exists()

    def test_build_lock_collapses_concurrent_rebuilds(self, tmp_path):
        """N concurrent build() calls produce ONE rebuild, not N."""
        import threading

        vault = _make_vault(tmp_path)
        for i in range(6):
            _write_page(vault, "sources", f"p{i}.md", f"Page {i}", f"Body content number {i} about agents.")
        embedder = _CountingEmbedder()
        idx = _make_index(vault, embedder)

        barrier = threading.Barrier(5)

        def _build():
            barrier.wait()
            idx.build()

        threads = [threading.Thread(target=_build) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 6 pages → with chunk_chars=400 each short page is one chunk → 6 chunks
        # → one build = ceil(6/8) = 1 embed batch. The herd must collapse to a
        # single real rebuild, so total batch calls stay at that single build's
        # count rather than 5x it.
        assert embedder.batch_calls == 1, f"expected herd collapse to 1 batch, got {embedder.batch_calls}"
        assert idx.matrix_path.exists()

    def test_cooldown_throttles_repeated_async_rebuilds(self, tmp_path):
        """A failing rebuild is not re-scheduled within the cooldown window."""
        vault = _make_vault(tmp_path)
        _write_page(vault, "sources", "x.md", "X", "Some body about agents and graphs.")
        embedder = _CountingEmbedder()
        idx = _make_index(vault, embedder)

        # Force build() to fail so the matrix never appears and _is_ready stays False.
        def _boom():
            raise RuntimeError("embeddings unavailable")

        idx._build_locked = _boom  # type: ignore[method-assign]

        key = str(idx.matrix_path)
        idx.ensure_built_async()
        # Wait out the (failing) background attempt.
        import time as _time

        deadline = _time.monotonic() + 2.0
        while _time.monotonic() < deadline:
            with vi._REBUILD_STATE_GUARD:
                done = key not in vi._REBUILD_IN_PROGRESS
            if done:
                break
            _time.sleep(0.02)

        with vi._REBUILD_STATE_GUARD:
            assert key not in vi._REBUILD_IN_PROGRESS  # finished (failed)
            assert key in vi._REBUILD_LAST_ATTEMPT  # attempt was recorded

        # A second immediate request must be throttled by the cooldown (no new thread).
        before = dict(vi._REBUILD_LAST_ATTEMPT)
        idx.ensure_built_async()
        with vi._REBUILD_STATE_GUARD:
            assert vi._REBUILD_LAST_ATTEMPT[key] == before[key], "cooldown should suppress re-scheduling"
