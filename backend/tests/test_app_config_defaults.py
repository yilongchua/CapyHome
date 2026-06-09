from src.config.app_config import AppConfig


def test_default_run_config_comes_from_session_config():
    config = AppConfig(
        sandbox={"use": "src.sandbox.local:LocalSandboxProvider"},
        channels={
            "session": {
                "config": {
                    "recursion_limit": 321,
                }
            }
        },
    )

    assert config.get_default_run_config() == {"recursion_limit": 321}
    assert config.get_default_recursion_limit() == 321


def test_deprecated_prompt_config_is_accepted_but_not_modeled():
    config = AppConfig(
        sandbox={"use": "src.sandbox.local:LocalSandboxProvider"},
        prompt={"componentized": False},
    )

    assert "prompt" not in AppConfig.model_fields
    assert config.model_extra == {"prompt": {"componentized": False}}
