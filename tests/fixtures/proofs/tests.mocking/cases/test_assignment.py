from agent_introspection import findings


def test_rebinds_an_owned_attribute() -> None:
    findings.refresh = lambda *args: {}
