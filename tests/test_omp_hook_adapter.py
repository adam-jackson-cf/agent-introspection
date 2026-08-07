import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ADAPTER = REPOSITORY_ROOT / ".agents/skills/introspection-onboarding/scripts/adapters/omp.ts"


@pytest.fixture
def omp_adapter_sandbox(tmp_path: Path) -> tuple[Path, Path]:
    adapter_dir = tmp_path / "scripts" / "adapters"
    adapter_dir.mkdir(parents=True)
    adapter = adapter_dir / "omp.ts"
    shutil.copyfile(ADAPTER, adapter)

    runtime = tmp_path / "scripts" / "session-context-runtime.sh"
    runtime.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$OMP_RUNTIME_LOG"\n'
        'exit "${OMP_RUNTIME_EXIT_STATUS:-0}"\n'
    )
    runtime.chmod(0o755)
    return adapter, tmp_path / "runtime-argv.txt"


def _invoke_adapter(
    tmp_path: Path,
    adapter: Path,
    runtime_log: Path,
    event: str,
    session_id: str,
    workspace: str,
    native_event: dict[str, object],
    runtime_status: int = 0,
) -> dict[str, str | None]:
    driver = tmp_path / "invoke.ts"
    driver.write_text(
        """
import { pathToFileURL } from "node:url";

const adapter = await import(pathToFileURL(process.argv[2]).href);
const handlers = new Map();
adapter.default({ on(event, handler) { handlers.set(event, handler); } });
try {
  handlers.get(process.argv[3])(
    JSON.parse(process.argv[6]),
    {
      cwd: process.argv[5],
      sessionManager: { getSessionId() { return process.argv[4]; } },
    },
  );
  console.log(JSON.stringify({ error: null }));
} catch (error) {
  console.log(JSON.stringify({ error: error instanceof Error ? error.message : String(error) }));
}
""".strip()
        + "\n"
    )
    bun = shutil.which("bun")
    assert bun is not None, "Bun is required to execute the OMP TypeScript extension adapter"
    completed = subprocess.run(
        [bun, "run", driver, adapter, event, session_id, workspace, json.dumps(native_event)],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "OMP_RUNTIME_LOG": str(runtime_log),
            "OMP_RUNTIME_EXIT_STATUS": str(runtime_status),
        },
    )
    return json.loads(completed.stdout)


@pytest.mark.parametrize(
    ("native_event", "expected_event"),
    (("session_start", "session_start"), ("session_shutdown", "session_end")),
)
def test_omp_lifecycle_events_invoke_canonical_runtime(
    tmp_path: Path,
    omp_adapter_sandbox: tuple[Path, Path],
    native_event: str,
    expected_event: str,
) -> None:
    adapter, runtime_log = omp_adapter_sandbox

    outcome = _invoke_adapter(
        tmp_path,
        adapter,
        runtime_log,
        native_event,
        "omp-session-42",
        "/workspaces/authoritative",
        {"timestamp": "2026-08-05T12:34:56.789Z"},
    )

    assert outcome == {"error": None}
    producer, session_id, event_type, occurred_at, workspace = runtime_log.read_text().splitlines()
    assert (producer, session_id, event_type, workspace) == (
        "omp",
        "omp-session-42",
        expected_event,
        "/workspaces/authoritative",
    )
    assert occurred_at == "2026-08-05T12:34:56.789Z"


@pytest.mark.parametrize(
    ("runtime_status", "expected_error"),
    (
        (65, None),
        (64, "OMP session-context runtime exited with status 64"),
        (70, "OMP session-context runtime exited with status 70"),
    ),
)
def test_omp_adapter_distinguishes_bounded_rejection_from_runtime_failure(
    tmp_path: Path,
    omp_adapter_sandbox: tuple[Path, Path],
    runtime_status: int,
    expected_error: str | None,
) -> None:
    adapter, runtime_log = omp_adapter_sandbox

    outcome = _invoke_adapter(
        tmp_path,
        adapter,
        runtime_log,
        "session_start",
        "omp-session-42",
        "/workspaces/authoritative",
        {"timestamp": "2026-08-05T12:34:56.789Z"},
        runtime_status,
    )

    assert outcome == {"error": expected_error}
    assert runtime_log.exists()


def test_omp_uses_synchronous_time_when_native_timestamp_is_absent(
    tmp_path: Path, omp_adapter_sandbox: tuple[Path, Path]
) -> None:
    adapter, runtime_log = omp_adapter_sandbox

    outcome = _invoke_adapter(
        tmp_path,
        adapter,
        runtime_log,
        "session_start",
        "omp-session-42",
        "/workspaces/authoritative",
        {},
    )

    assert outcome == {"error": None}
    assert (
        datetime.fromisoformat(
            runtime_log.read_text().splitlines()[3].replace("Z", "+00:00")
        ).tzinfo
        == UTC
    )


@pytest.mark.parametrize(
    ("session_id", "workspace", "required_value"),
    (("", "/workspaces/authoritative", "session id"), ("omp-session-42", "", "workspace")),
)
def test_omp_adapter_rejects_missing_authoritative_values(
    tmp_path: Path,
    omp_adapter_sandbox: tuple[Path, Path],
    session_id: str,
    workspace: str,
    required_value: str,
) -> None:
    adapter, runtime_log = omp_adapter_sandbox

    outcome = _invoke_adapter(
        tmp_path,
        adapter,
        runtime_log,
        "session_start",
        session_id,
        workspace,
        {},
    )

    assert outcome == {
        "error": f"OMP {required_value} is required and must not contain control characters"
    }
    assert not runtime_log.exists()


@pytest.mark.parametrize(
    ("session_id", "workspace", "native_event", "message"),
    (
        (
            "id\nother",
            "/workspaces/authoritative",
            {},
            "OMP session id is required and must not contain control characters",
        ),
        ("omp-session-42", "workspace", {}, "OMP workspace must be an absolute path"),
        (
            "omp-session-42",
            "/workspaces/authoritative",
            {"timestamp": "arbitrary"},
            "OMP timestamp must be RFC 3339",
        ),
        (
            "omp-session-42",
            "/workspaces/authoritative",
            {"timestamp": None},
            "OMP timestamp is required and must not contain control characters",
        ),
    ),
)
def test_omp_adapter_rejects_malformed_native_values_without_runtime_invocation(
    tmp_path: Path,
    omp_adapter_sandbox: tuple[Path, Path],
    session_id: str,
    workspace: str,
    native_event: dict[str, object],
    message: str,
) -> None:
    adapter, runtime_log = omp_adapter_sandbox

    outcome = _invoke_adapter(
        tmp_path,
        adapter,
        runtime_log,
        "session_start",
        session_id,
        workspace,
        native_event,
    )

    assert outcome == {"error": message}
    assert not runtime_log.exists()
