"""Verify make_work_agent picks the right prompt template based on current_mode.

The frontend currently addresses the ``work_agent`` LangGraph graph for all
requests, including plan-mode ones (it sets ``current_mode="plan"`` in
configurable). This test guards against regression of the bug where
``make_work_agent`` would always use the work-mode prompt, dropping the
plan-mode overlay even when the runtime is in plan mode.
"""

from __future__ import annotations

import pytest

from src.agents.work_agent import agent as work_agent_module
from src.config.app_config import AppConfig
from src.config.model_config import ModelConfig
from src.config.sandbox_config import SandboxConfig


def _make_app_config() -> AppConfig:
    return AppConfig(
        models=[
            ModelConfig(
                name="default-model",
                display_name="default-model",
                description=None,
                use="langchain_openai:ChatOpenAI",
                model="default-model",
                supports_thinking=False,
                supports_vision=False,
            )
        ],
        sandbox=SandboxConfig(use="src.sandbox.local:LocalSandboxProvider"),
    )


def _patch_factory_deps(monkeypatch):
    monkeypatch.setattr(work_agent_module, "get_app_config", lambda: _make_app_config())
    import src.tools as tools_module

    monkeypatch.setattr(tools_module, "get_available_tools", lambda **kwargs: [])
    monkeypatch.setattr(
        work_agent_module,
        "_build_middlewares",
        lambda config, model_name, agent_name=None, model_router=None: [],
    )
    monkeypatch.setattr(work_agent_module, "create_chat_model", lambda **kwargs: object())
    monkeypatch.setattr(work_agent_module, "create_agent", lambda **kwargs: kwargs)


def test_make_work_agent_uses_plan_prompt_when_current_mode_is_plan(monkeypatch):
    _patch_factory_deps(monkeypatch)

    result = work_agent_module.make_work_agent(
        {
            "configurable": {
                "model_name": "default-model",
                "current_mode": "plan",
                "subagent_enabled": False,
            }
        }
    )

    rendered_prompt = result["system_prompt"]
    assert "<plan_mode>" in rendered_prompt, "plan-mode section missing when current_mode='plan'"
    assert "Your ONLY job is to produce a plan.md" in rendered_prompt


def test_make_work_agent_omits_plan_prompt_when_current_mode_is_work(monkeypatch):
    _patch_factory_deps(monkeypatch)

    result = work_agent_module.make_work_agent(
        {
            "configurable": {
                "model_name": "default-model",
                "current_mode": "work",
                "subagent_enabled": False,
            }
        }
    )

    rendered_prompt = result["system_prompt"]
    assert "<plan_mode>" not in rendered_prompt, "plan-mode section leaked into work-mode prompt"


def test_make_work_agent_falls_back_to_legacy_is_plan_mode(monkeypatch):
    """Legacy callers may set ``is_plan_mode=True`` without ``current_mode``."""
    _patch_factory_deps(monkeypatch)

    result = work_agent_module.make_work_agent(
        {
            "configurable": {
                "model_name": "default-model",
                "is_plan_mode": True,
                "subagent_enabled": False,
            }
        }
    )

    rendered_prompt = result["system_prompt"]
    assert "<plan_mode>" in rendered_prompt, "legacy is_plan_mode=True should still trigger plan-mode prompt"


def test_make_plan_agent_invokes_plan_prompt(monkeypatch):
    """Verify make_plan_agent end-to-end picks the plan-mode template."""
    from src.agents.plan_agent import agent as plan_agent_module

    _patch_factory_deps(monkeypatch)

    result = plan_agent_module.make_plan_agent(
        {
            "configurable": {
                "model_name": "default-model",
                "subagent_enabled": False,
            }
        }
    )

    rendered_prompt = result["system_prompt"]
    assert "<plan_mode>" in rendered_prompt


def test_explicit_prompt_template_fn_overrides_mode_autodetect(monkeypatch):
    """When make_plan_agent passes prompt_template_fn explicitly, mode autodetect is bypassed."""
    _patch_factory_deps(monkeypatch)

    sentinel = "SENTINEL_PROMPT_FROM_EXPLICIT_TEMPLATE"

    def explicit_template(**kwargs):
        return sentinel

    result = work_agent_module._build_work_agent(
        {
            "configurable": {
                "model_name": "default-model",
                "current_mode": "plan",  # would normally trigger plan template
                "subagent_enabled": False,
            }
        },
        prompt_template_fn=explicit_template,
    )

    assert result["system_prompt"] == sentinel
