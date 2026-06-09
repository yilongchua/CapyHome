from __future__ import annotations

from src.agents.work_agent import prompt_cache


def test_prompt_cache_evicts_oldest_entry_when_bounded(monkeypatch):
    prompt_cache.invalidate()
    monkeypatch.setattr(prompt_cache, "MAX_CACHE_ENTRIES", 2)

    build_calls: list[str] = []

    def build(label: str):
        def _inner():
            build_calls.append(label)
            return f"prompt-{label}"

        return _inner

    assert prompt_cache.get_cached_prompt(build("a"), None, False, 1, {"a"}, True) == "prompt-a"
    assert prompt_cache.get_cached_prompt(build("b"), None, False, 1, {"b"}, True) == "prompt-b"
    assert prompt_cache.get_cached_prompt(build("c"), None, False, 1, {"c"}, True) == "prompt-c"

    assert len(prompt_cache._cache) == 2
    assert prompt_cache.get_cached_prompt(build("a2"), None, False, 1, {"a"}, True) == "prompt-a2"
    assert build_calls == ["a", "b", "c", "a2"]
