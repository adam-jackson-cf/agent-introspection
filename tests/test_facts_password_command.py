import pytest

from agent_introspection import facts
from agent_introspection.config import ConfigurationError, SigNozConfig

pytestmark = pytest.mark.boundary


def test_password_command_output_is_the_password_and_never_in_errors() -> None:
    ok = SigNozConfig(clickhouse_url="https://ch", clickhouse_password_command=("echo", "s3cret"))
    assert facts.clickhouse_password(ok) == "s3cret"
    failing = SigNozConfig(
        clickhouse_url="https://ch", clickhouse_password_command=("sh", "-c", "echo leak; exit 3")
    )
    with pytest.raises(ConfigurationError) as error:
        facts.clickhouse_password(failing)
    assert "leak" not in str(error.value)
