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

