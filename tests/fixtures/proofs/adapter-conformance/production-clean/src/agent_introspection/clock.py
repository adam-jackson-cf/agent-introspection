import os


def config_path():
    return os.environ.get("AGENT_INTROSPECTION_CONFIG", "config.toml")
