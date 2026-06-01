"""Regression tests for the vault lint LLM-judge response parser.

The judge prompt asks the model for a bare JSON array. A prior version routed
the response through an object-only extractor (``{...}``), so every array
response parsed to zero verdicts and ``use_llm=True`` silently no-op'd. These
tests lock in array handling (bare / single / fenced / reasoning-wrapped) plus
the object-wrapped fallback.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import pytest

from src.control_plane.vault_learning import VaultLearningManager


@pytest.fixture
def vault(tmp_path: Path) -> VaultLearningManager:
    return VaultLearningManager(vault_root=tmp_path)


def test_parses_bare_json_array(vault: VaultLearningManager) -> None:
    raw = (
        '[{"slug":"a","verdict":"keep","reason":"x"},'
        '{"slug":"b","verdict":"remove","reason":"news site"}]'
    )
    out = vault._parse_judge_response(raw)
    assert out == {
        "a": {"verdict": "keep", "reason": "x"},
        "b": {"verdict": "remove", "reason": "news site"},
    }


def test_parses_single_item_array(vault: VaultLearningManager) -> None:
    out = vault._parse_judge_response('[{"slug":"c","verdict":"remove","reason":"stopword"}]')
    assert out == {"c": {"verdict": "remove", "reason": "stopword"}}


def test_parses_markdown_fenced_array(vault: VaultLearningManager) -> None:
    raw = '```json\n[{"slug":"d","verdict":"keep","reason":"ok"}]\n```'
    out = vault._parse_judge_response(raw)
    assert out == {"d": {"verdict": "keep", "reason": "ok"}}


def test_parses_reasoning_wrapped_array(vault: VaultLearningManager) -> None:
    raw = '<think>let me weigh these...</think>\n[{"slug":"e","verdict":"remove","reason":"generic"}]'
    out = vault._parse_judge_response(raw)
    assert out == {"e": {"verdict": "remove", "reason": "generic"}}


def test_object_wrapped_fallback(vault: VaultLearningManager) -> None:
    raw = '{"results":[{"slug":"f","verdict":"keep","reason":"y"}]}'
    out = vault._parse_judge_response(raw)
    assert out == {"f": {"verdict": "keep", "reason": "y"}}


def test_invalid_verdicts_and_garbage_are_dropped(vault: VaultLearningManager) -> None:
    # unknown verdict + missing slug are skipped; prose returns nothing.
    raw = '[{"slug":"g","verdict":"maybe"},{"verdict":"keep"},{"slug":"h","verdict":"keep","reason":"ok"}]'
    assert vault._parse_judge_response(raw) == {"h": {"verdict": "keep", "reason": "ok"}}
    assert vault._parse_judge_response("I cannot help with that.") == {}
    assert vault._parse_judge_response("") == {}


class _Resp:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeModel:
    """Echoes a keep-verdict array for whatever slugs appear in the prompt,
    and records how many distinct threads invoked it."""

    def __init__(self) -> None:
        self.threads: set[str] = set()
        self._lock = threading.Lock()

    def invoke(self, prompt: str):  # noqa: ANN201
        with self._lock:
            self.threads.add(threading.current_thread().name)
        slugs = re.findall(r"slug=(\S+)", prompt)
        return _Resp(json.dumps([{"slug": s, "verdict": "keep", "reason": "ok"} for s in slugs]))


def _pages(n: int) -> list[dict]:
    return [
        {"slug": f"p{i}", "kind": "entity", "label": f"P{i}",
         "live_source_count": 1, "is_stub": True, "body_excerpt": "x", "source_titles": []}
        for i in range(n)
    ]


@pytest.mark.parametrize("workers", [1, 4])
def test_judge_merges_every_batch(vault: VaultLearningManager, monkeypatch, workers: int) -> None:
    fake = _FakeModel()
    monkeypatch.setattr(
        "src.control_plane.vault_learning._lint.create_chat_model",
        lambda **kwargs: fake,
    )
    pages = _pages(50)
    out = vault._judge_pages_with_llm(
        pages, user_context={}, vault_context={}, batch_size=5, max_workers=workers,
    )
    # No verdict is lost regardless of worker count.
    assert len(out) == 50
    assert all(v["verdict"] == "keep" for v in out.values())
    assert {p["slug"] for p in pages} == set(out)
    if workers > 1:
        # Parallel path actually used more than one worker thread.
        assert len(fake.threads) > 1


def test_judge_stops_on_cancel(vault: VaultLearningManager, monkeypatch) -> None:
    fake = _FakeModel()
    monkeypatch.setattr(
        "src.control_plane.vault_learning._lint.create_chat_model",
        lambda **kwargs: fake,
    )
    checks = {"n": 0}

    def should_cancel() -> bool:
        # Let the first batch run, then request cancel before the second.
        checks["n"] += 1
        return checks["n"] > 1

    out = vault._judge_pages_with_llm(
        _pages(50), user_context={}, vault_context={}, batch_size=5,
        max_workers=1, should_cancel=should_cancel,
    )
    # Cancelled after the first batch -> far fewer than all 50 verdicts.
    assert 0 < len(out) < 50


def test_judge_reports_progress(vault: VaultLearningManager, monkeypatch) -> None:
    fake = _FakeModel()
    monkeypatch.setattr(
        "src.control_plane.vault_learning._lint.create_chat_model",
        lambda **kwargs: fake,
    )
    seen: list[tuple[int, int]] = []
    vault._judge_pages_with_llm(
        _pages(50), user_context={}, vault_context={}, batch_size=10,
        max_workers=1, progress_callback=lambda done, total: seen.append((done, total)),
    )
    assert seen[-1] == (50, 50)
    assert [done for done, _ in seen] == [10, 20, 30, 40, 50]


def test_judge_workers_are_capped(vault: VaultLearningManager, monkeypatch) -> None:
    fake = _FakeModel()
    monkeypatch.setattr(
        "src.control_plane.vault_learning._lint.create_chat_model",
        lambda **kwargs: fake,
    )
    # batch_size=1 over 30 pages = 30 batches; ask for absurd worker count.
    out = vault._judge_pages_with_llm(
        _pages(30), user_context={}, vault_context={}, batch_size=1, max_workers=999,
    )
    assert len(out) == 30
    assert len(fake.threads) <= VaultLearningManager._MAX_JUDGE_WORKERS
