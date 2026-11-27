from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.boundary

RUNTIME = Path(__file__).parents[1] / "src/agent_introspection/hook_runtime"


def _run_activity_shim(fake: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", str(RUNTIME / "activity-shim.sh"), "claude-code", "PreToolUse"],
        input='{"session_id": "s"}',
        capture_output=True,
        text=True,
        env={**os.environ, "AGENT_INTROSPECTION_BIN": str(fake)},
        timeout=10,
        check=False,
    )


def _pause() -> None:
    subprocess.run(["sleep", "0.05"], check=True, timeout=10)


def _run_bun_probe(probe: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bun", str(probe)], env=env, capture_output=True, text=True, timeout=60, check=True
    )


def test_shim_hands_stdin_to_a_detached_cli_and_prints_nothing(tmp_path: Path) -> None:
    received = tmp_path / "received"
    fake = tmp_path / "fake-cli"
    fake.write_text(f'#!/bin/sh\ncat > "{received}.part"; mv "{received}.part" "{received}"\n')
    fake.chmod(0o755)
    result = _run_activity_shim(fake)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    for _ in range(100):
        if received.exists():
            break
        _pause()
    assert received.read_text() == '{"session_id": "s"}'


OMP_PROBE = """
import register from "%s";
let sent = 0;
globalThis.fetch = (() => { sent += 1; return Promise.resolve(new Response("")); }) as any;
const handlers: Record<string, any> = {};
register({ on: (name: string, fn: any) => { handlers[name] = fn; } } as any);
handlers.before_agent_start(
  { prompt: "secret prompt" },
  { sessionManager: { getSessionId: () => "s1" } },
);
console.log(sent);
"""


OMP_RESULT_PROBE = """
import register from "%s";
const handlers: Record<string, any> = {};
register({ on: (name: string, fn: any) => { handlers[name] = fn; } } as any);
const context = { sessionManager: { getSessionId: () => "s1" }, cwd: "/Users/me/p" };
handlers.tool_result({ toolCallId: "c0", toolName: "bash", isError: false, content: [] }, context);
handlers.tool_result(
  { toolCallId: "c1", toolName: "bash", isError: undefined, content: [] },
  context,
);
handlers.tool_result(
  {
    toolCallId: "c2",
    toolName: "bash",
    isError: true,
    content: [{ type: "text", text: "boom: ModuleNotFoundError" }, { type: "image" }],
    details: { exitCode: 2 },
  },
  context,
);
await new Promise((resolve) => setTimeout(resolve, 1500));
"""


def test_omp_tool_result_spawns_the_shim_only_for_errors(tmp_path: Path) -> None:
    shim = tmp_path / "shim.sh"
    sink = tmp_path / "sink"
    shim.write_text(f'echo "$2" >> {sink}\ncat >> {sink}\necho >> {sink}\n')
    probe = tmp_path / "probe.ts"
    probe.write_text(OMP_RESULT_PROBE % (RUNTIME / "omp/activity.ts"))
    env = {**os.environ, "AGENT_INTROSPECTION_SHIM": str(shim)}
    _run_bun_probe(probe, env)

    lines = sink.read_text().split()
    assert lines.count("tool_result") == 1
    (envelope,) = [
        json.loads(line) for line in sink.read_text().splitlines() if line.startswith("{")
    ]
    assert envelope["tool_use_id"] == "c2"
    assert envelope["error"] == "boom: ModuleNotFoundError\n"
    assert envelope["exit_code"] == 2


OMP_FAILURE_PROBE = """
import register from "%s";
globalThis.fetch = (() => Promise.reject(new TypeError("fetch failed"))) as any;
const handlers: Record<string, any> = {};
register({ on: (name: string, fn: any) => { handlers[name] = fn; } } as any);
const broken = { sessionManager: { getSessionId: () => { throw new RangeError("no session"); } } };
handlers.tool_call({ toolCallId: "c1", toolName: "bash", input: {} }, broken);
handlers.before_agent_start(
  { prompt: "secret prompt" },
  { sessionManager: { getSessionId: () => "s1" } },
);
await new Promise((resolve) => setTimeout(resolve, 100));
"""


def test_omp_emit_and_prompt_export_failures_reach_the_hook_log(tmp_path: Path) -> None:
    probe = tmp_path / "probe.ts"
    probe.write_text(OMP_FAILURE_PROBE % (RUNTIME / "omp/activity.ts"))
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:9",
        "AGENT_INTROSPECTION_OMP_PROMPT_EXPORT": "1",
        "AGENT_INTROSPECTION_SHIM": str(probe),
    }
    _run_bun_probe(probe, env)

    log = (tmp_path / ".local/share/agent-introspection/hooks.log").read_text()
    events = [line.split(" ", 1)[1] for line in log.splitlines()]
    assert events == ["omp tool_call emit RangeError", "omp before_agent_start otlp TypeError"]
    assert "secret prompt" not in log


@pytest.mark.parametrize(("opt_in", "expected"), [(None, "0"), ("1", "1")])
def test_omp_prompt_is_exported_only_after_opt_in(
    tmp_path: Path, opt_in: str | None, expected: str
) -> None:
    probe = tmp_path / "probe.ts"
    probe.write_text(OMP_PROBE % (RUNTIME / "omp/activity.ts"))
    env = {
        **os.environ,
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:9",
        "AGENT_INTROSPECTION_SHIM": "/nonexistent",
    }
    env.pop("AGENT_INTROSPECTION_OMP_PROMPT_EXPORT", None)
    if opt_in is not None:
        env["AGENT_INTROSPECTION_OMP_PROMPT_EXPORT"] = opt_in
    result = _run_bun_probe(probe, env)
    assert result.stdout.strip() == expected
