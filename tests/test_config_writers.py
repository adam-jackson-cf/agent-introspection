from __future__ import annotations

import json
import os
import tomllib
from datetime import UTC, date, datetime, time
from pathlib import Path

import pytest

from agent_introspection import config_writers
from agent_introspection.config_writers import ConfigFileError

_HTTP_LOGS = {"otlp-http": {"endpoint": "http://localhost:4318/v1/logs", "protocol": "binary"}}

_CODEX = """\
# Codex settings
model = "gpt-5"

[otel]
environment = "dev" # kept
log_user_prompt = true
exporter = { otlp-grpc = { endpoint = "http://localhost:4317" } } # the logs exporter

# Notes about projects, kept
[projects."/work/repo"]
trust_level = "trusted"
"""


def test_toml_edits_the_managed_key_line_in_place_and_keeps_everything_else() -> None:
    update = config_writers.update_table(
        _CODEX, "toml", "otel", {"exporter": _HTTP_LOGS, "trace_exporter": _HTTP_LOGS}
    )

    updated = update.text
    assert update.notes == ()
    lines = updated.splitlines()
    assert lines[:6] == _CODEX.splitlines()[:6]
    assert lines[6] == (
        'exporter = { otlp-http = { endpoint = "http://localhost:4318/v1/logs", '
        'protocol = "binary" } } # the logs exporter'
    )
    assert lines[7].startswith("trace_exporter = { otlp-http = ")
    assert updated.endswith(_CODEX[_CODEX.index("\n# Notes") :])
    document = tomllib.loads(updated)
    assert document["otel"]["environment"] == "dev"
    assert document["projects"] == {"/work/repo": {"trust_level": "trusted"}}


def test_toml_appends_an_absent_table() -> None:
    text = 'model = "gpt-5"\n\n[tui]\nnotifications = true'

    updated = config_writers.update_table(text, "toml", "otel", {"exporter": _HTTP_LOGS}).text

    assert updated.startswith(text + "\n\n[otel]\nexporter = { otlp-http = ")
    assert tomllib.loads(updated)["otel"] == {"exporter": _HTTP_LOGS}
    assert config_writers.update_table("", "toml", "otel", {"log_user_prompt": True}) == (
        config_writers.TableUpdate("[otel]\nlog_user_prompt = true\n")
    )


@pytest.mark.parametrize(
    "otel",
    [
        # Sub-tables of the managed table.
        '[otel]\nenvironment = "dev"\n\n[otel.exporter.otlp-grpc]\nendpoint = "http://localhost:4317"\n',
        # The managed key written as dotted keys.
        '[otel]\nenvironment = "dev"\nexporter.otlp-grpc.endpoint = "http://localhost:4317"\n',
        # The managed key's value spread over several lines.
        '[otel]\nenvironment = "dev"\n'
        'exporter = { otlp-grpc = { endpoint = """\nhttp://localhost:4317""" } }\n',
    ],
)
def test_toml_regenerates_a_table_it_cannot_edit_line_by_line(otel: str) -> None:
    text = f"# top\nmodel = 'gpt-5'\n\n{otel}\n# after\n[tui]\nnotifications = true\n"

    update = config_writers.update_table(text, "toml", "otel", {"exporter": _HTTP_LOGS})

    updated = update.text
    (note,) = update.notes
    assert note.startswith("[otel] could not be edited line by line")
    assert tomllib.loads(updated) == {
        "model": "gpt-5",
        "otel": {"environment": "dev", "exporter": _HTTP_LOGS},
        "tui": {"notifications": True},
    }
    assert updated.startswith("# top\nmodel = 'gpt-5'\n\n[otel]\n")
    assert "# after\n[tui]\n" in updated
    assert "[otel." not in updated


_SPLIT_OTEL = """\
[otel] # telemetry
# the deployment name
environment = "dev" # dev or prod
note = \"\"\"
# not a comment
nor # this\"\"\" # but this is

# where logs go
[otel.exporter.otlp-grpc]
# the collector on this machine
endpoint = "http://localhost:4317" # gRPC
headers = [
  "a", # first
  # between
  "b",
]
"""


def test_a_regenerated_table_keeps_its_comments_above_the_key_each_described() -> None:
    text = f"{_SPLIT_OTEL}\n# after\n[tui]\nx = 1\n"

    update = config_writers.update_table(text, "toml", "otel", {"log_user_prompt": True})

    assert update.text.startswith(
        "[otel]\n"
        "# telemetry\n"
        "# the deployment name\n"
        "# dev or prod\n"
        'environment = "dev"\n'
        "# but this is\n"
        'note = "# not a comment\\nnor # this"\n'
        "# where logs go\n"
        "# the collector on this machine\n"
        "# gRPC\n"
        "# first\n"
        "# between\n"
        "exporter = { otlp-grpc = "
    )
    assert update.text.endswith("log_user_prompt = true\n\n# after\n[tui]\nx = 1\n")
    assert tomllib.loads(update.text)["otel"]["note"] == "# not a comment\nnor # this"
    (note,) = update.notes
    assert note.endswith("its 9 comment line(s) now sit above the key each described")


def test_toml_ignores_header_like_lines_inside_multiline_strings() -> None:
    text = '[otel]\nnote = """\n[otel.exporter]\nexporter = 1\n"""\n\n[tui]\nx = 1\n'

    updated = config_writers.update_table(text, "toml", "otel", {"exporter": _HTTP_LOGS}).text

    document = tomllib.loads(updated)
    assert document["otel"]["note"] == "[otel.exporter]\nexporter = 1\n"
    assert document["otel"]["exporter"] == _HTTP_LOGS
    assert document["tui"] == {"x": 1}


@pytest.mark.parametrize(
    "text",
    [
        'otel = { environment = "dev" }\n',
        'otel.environment = "dev"\n',
    ],
)
def test_toml_refuses_a_table_written_as_an_inline_or_dotted_key(text: str) -> None:
    with pytest.raises(ConfigFileError, match="change it by hand"):
        config_writers.update_table(text, "toml", "otel", {"exporter": _HTTP_LOGS})


def test_toml_edits_top_level_keys_and_keeps_crlf_line_endings() -> None:
    text = "# settings\r\nmodel = 'a'\r\n\r\n[tui]\r\nx = 1\r\n"

    updated = config_writers.update_table(text, "toml", "", {"model": "b", "effort": "high"})

    assert updated.text == (
        '# settings\r\nmodel = "b"\r\neffort = "high"\r\n\r\n[tui]\r\nx = 1\r\n'
    )
    assert config_writers.update_table("[otel]\nx = 1", "toml", "otel", {"y": 2}).text == (
        "[otel]\nx = 1\ny = 2\n"
    )


@pytest.mark.parametrize(
    "value",
    [
        "plain",
        'quote " backslash \\ tab \t newline \n bell \x07 delete \x7f',
        True,
        False,
        -42,
        1.5,
        1e300,
        float("inf"),
        float("-inf"),
        [1, "two", [3]],
        {},
        {"bare-key": {"key with space": "x", "n": 1}},
        date(2026, 10, 13),
        time(12, 30, 5),
        datetime(2026, 10, 13, 12, 30, tzinfo=UTC),
    ],
)
def test_toml_values_round_trip(value: object) -> None:
    assert tomllib.loads(f"v = {config_writers.toml_value(value)}")["v"] == value


def test_toml_nan_and_unsupported_values() -> None:
    assert config_writers.toml_value(float("nan")) == "nan"
    with pytest.raises(ConfigFileError, match="cannot write a NoneType"):
        config_writers.toml_value(None)


def test_toml_reads_the_table_and_rejects_bad_input() -> None:
    assert config_writers.read_table(_CODEX, "toml", "otel")["log_user_prompt"] is True
    with pytest.raises(ConfigFileError, match="otel is not a table"):
        config_writers.update_table('[[otel]]\nenvironment = "dev"\n', "toml", "otel", {"a": 1})
    assert config_writers.read_table(_CODEX, "toml", "absent") == {}
    with pytest.raises(ConfigFileError, match="not valid TOML"):
        config_writers.read_table("[otel", "toml", "otel")
    with pytest.raises(ConfigFileError, match="model is not a table"):
        config_writers.read_table(_CODEX, "toml", "model.x")


@pytest.mark.parametrize(
    ("indent", "text"),
    [
        ("    ", '{\n    "theme": "dark",\n    "env": {\n        "A": "1"\n    }\n}\n'),
        ("\t", '{\n\t"theme": "dark",\n\t"env": {\n\t\t"A": "1"\n\t}\n}'),
    ],
)
def test_json_keeps_the_indentation_order_and_unmanaged_values(indent: str, text: str) -> None:
    updated = config_writers.update_table(text, "json", "env", {"B": "2", "A": "3"})

    assert updated.text == (
        json.dumps({"theme": "dark", "env": {"A": "3", "B": "2"}}, indent=indent)
        + ("\n" if text.endswith("\n") else "")
    )


def test_json_creates_tables_and_keeps_a_one_line_document_compact() -> None:
    assert config_writers.update_table("", "json", "env", {"A": "1"}).text == (
        '{\n  "env": {\n    "A": "1"\n  }\n}\n'
    )
    assert config_writers.update_table('{"theme": "é"}', "json", "a.b", {"A": "1"}).text == (
        '{"theme": "é", "a": {"b": {"A": "1"}}}'
    )


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("{", "not valid JSON"),
        ("[]", "must hold a JSON object"),
        ('{"env": "x"}', "env is not a table"),
    ],
)
def test_json_rejects_what_it_cannot_change(text: str, message: str) -> None:
    with pytest.raises(ConfigFileError, match=message):
        config_writers.update_table(text, "json", "env", {"A": "1"})


_DOTENV = """\
# omp environment
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 # old
OTEL_SERVICE_NAME="oh-my-pi"
SINGLE='a b' # comment
ESCAPED="say \\"hi\\"\\nbye"
OPENROUTER_API_KEY=sk-sentinel
GLUED="x"#tight
"""


def test_dotenv_reads_quoted_and_bare_values() -> None:
    values = config_writers.read_table(_DOTENV, "dotenv", "")

    assert values["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://localhost:4317"
    assert values["OTEL_SERVICE_NAME"] == "oh-my-pi"
    assert values["SINGLE"] == "a b"
    assert values["ESCAPED"] == 'say "hi"\nbye'


def test_dotenv_updates_lines_in_place_and_appends_missing_keys() -> None:
    values: dict[str, object] = {
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318",
        "SINGLE": "c d",
        "GLUED": "y",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
        "NOTE": 'two words "quoted"',
        "FLAG": True,
    }
    updated = config_writers.update_table(_DOTENV.rstrip("\n"), "dotenv", "", values)

    assert updated.notes == ()
    lines = updated.text.splitlines()
    original = _DOTENV.splitlines()
    # A rewritten line keeps its trailing comment; only the value changes.
    assert lines[1] == "export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 # old"
    assert lines[3] == 'SINGLE="c d" # comment'
    assert lines[6] == "GLUED=y #tight"
    assert [lines[0], lines[2], *lines[4:6]] == [original[0], original[2], *original[4:6]]
    assert lines[7:] == [
        "OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf",
        'NOTE="two words \\"quoted\\""',
        "FLAG=true",
    ]
    assert config_writers.read_table(updated.text, "dotenv", "") == {
        **config_writers.read_table(_DOTENV, "dotenv", ""),
        **values,
        "FLAG": "true",
    }
    with pytest.raises(ConfigFileError, match="cannot be a table"):
        config_writers.update_table("", "dotenv", "", {"A": {"b": 1}})


def test_backup_and_atomic_write_keep_the_original_and_its_mode(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("before")
    os.chmod(path, 0o640)

    copy = config_writers.backup(path, datetime(2026, 10, 13, 12, 0, tzinfo=UTC))
    config_writers.atomic_write(path, "after", 0o640)

    assert copy.name == "settings.json.bak-20261013T120000000000Z"
    assert copy.read_text() == "before"
    assert path.read_text() == "after"
    assert path.stat().st_mode & 0o777 == 0o640
    assert sorted(item.name for item in tmp_path.iterdir()) == ["settings.json", copy.name]
