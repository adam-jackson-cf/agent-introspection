from agent_introspection import config


def test_config_path_comes_from_the_loader():
    config.load = dict
