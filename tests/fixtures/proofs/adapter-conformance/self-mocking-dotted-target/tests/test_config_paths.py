def test_config_path_comes_from_the_loader(monkeypatch):
    monkeypatch.setattr("agent_introspection.config.load", dict)
