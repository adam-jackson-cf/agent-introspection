#!/usr/bin/env python3
"""Install Codex app-server lifecycle hooks for Codex Desktop."""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Final

_LABEL: Final = "com.adamjackson.agent-introspection-codex-app-server"
_MANAGED_RUNTIME: Final = Path(".local/lib/agent-introspection/session-context-runtime-v1")
_DEFAULT_CODEX_HOME: Final = Path(".codex")
_LEGACY_ROOT_PROXY: Final = "codex-app-server-proxy.py"
_LEGACY_NESTED_PROXY: Final = "proxy.py"
_LEGACY_REAL_CLI: Final = "codex-app-server-real-cli"
_LEGACY_NESTED_ADAPTER: Final = "adapter.sh"
_LEGACY_ROOT_ADAPTER: Final = "adapters/codex-app-server.sh"
_LEGACY_THREAD_STATE: Final = Path(".local/state/agent-introspection/codex-app-server-threads")
_START_MATCHER: Final = "^(startup|resume|clear|compact)$"
_END_MATCHER: Final = "^other$"


class InstallError(RuntimeError):
    """The managed app-server integration could not be installed safely."""


def _codex_config_root(home: Path) -> Path:
    override = os.environ.get("CODEX_HOME")
    if override is None:
        return home / _DEFAULT_CODEX_HOME
    if not override:
        raise InstallError("CODEX_HOME must not be empty")

    root = Path(override)
    if not root.is_absolute():
        raise InstallError("CODEX_HOME must be an absolute path")
    if not root.parent.is_dir():
        raise InstallError("CODEX_HOME parent must be an existing directory")
    if root.exists() and not root.is_dir():
        raise InstallError("CODEX_HOME must be a directory")
    return root


def _atomic_install(source: Path, destination: Path, mode: int) -> None:
    if not source.is_file():
        raise InstallError(f"required source file is unavailable: {source.name}")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}-", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as input_file, os.fdopen(descriptor, "wb") as output_file:
            shutil.copyfileobj(input_file, output_file)
            output_file.flush()
            os.fsync(output_file.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write(destination: Path, content: bytes, mode: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}-", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output_file:
            output_file.write(content)
            output_file.flush()
            os.fsync(output_file.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in pairs:
        if key in payload:
            raise InstallError("existing Codex hooks configuration contains duplicate keys")
        payload[key] = value
    return payload


def _validate_hook_command(command: object) -> None:
    if not isinstance(command, dict) or not isinstance(command.get("type"), str):
        raise InstallError("existing Codex hook command shape is invalid")


def _validate_hook_entry(entry: object) -> None:
    if not isinstance(entry, dict):
        raise InstallError("existing Codex hook entries must be objects")
    matcher = entry.get("matcher")
    commands = entry.get("hooks")
    if (matcher is not None and not isinstance(matcher, str)) or not isinstance(commands, list):
        raise InstallError("existing Codex hook entry shape is invalid")
    for command in commands:
        _validate_hook_command(command)


def _validate_hooks(hooks: object) -> None:
    if not isinstance(hooks, dict):
        raise InstallError("existing Codex hooks field must be an object")
    for event, entries in hooks.items():
        if not isinstance(event, str) or not isinstance(entries, list):
            raise InstallError("existing Codex hook event lists are invalid")
        for entry in entries:
            _validate_hook_entry(entry)


def _load_hooks(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except (OSError, json.JSONDecodeError) as error:
        raise InstallError("existing Codex hooks configuration is invalid") from error
    if not isinstance(payload, dict):
        raise InstallError("existing Codex hooks configuration must be an object")
    _validate_hooks(payload.get("hooks", {}))
    return payload


def _owned_entry(matcher: str, adapter: Path) -> dict[str, object]:
    return {
        "matcher": matcher,
        "hooks": [
            {
                "type": "command",
                "command": f"/usr/bin/python3 {shlex.quote(str(adapter))}",
            }
        ],
    }


def _is_owned(entry: dict[str, object], expected: dict[str, object]) -> bool:
    return entry == expected


def _command_tokens(command: str, adapter: Path) -> list[str] | None:
    if str(adapter) not in command:
        return None
    try:
        syntax = subprocess.run(
            ["/bin/sh", "-n"],
            input=command,
            text=True,
            check=False,
            capture_output=True,
        )
        if syntax.returncode != 0:
            raise InstallError("existing managed Codex hook command syntax is invalid")
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError as error:
        raise InstallError("existing managed Codex hook command syntax is invalid") from error


def _is_python_interpreter(token: str) -> bool:
    return token in {"python", "python3"} or (
        token.startswith("/") and Path(token).name.startswith("python")
    )


def _python_script_index(tokens: list[str], index: int) -> int | None:
    option_index = index + 1
    while option_index < len(tokens):
        option = tokens[option_index]
        if option == "--":
            return option_index + 1
        if not option.startswith("-") or option == "-":
            return option_index
        if option in {"-c", "-m"} or option.startswith(("-c", "-m")):
            return None
        if option in {"-W", "-X", "--check-hash-based-pycs"}:
            option_index += 1
        option_index += 1
    return None


def _is_assignment(token: str) -> bool:
    name, separator, _ = token.partition("=")
    return bool(separator) and name.isascii() and name.isidentifier()


def _env_command_index(tokens: list[str], index: int) -> int | None:
    option_index = index + 1
    options_with_argument = {
        "-C",
        "-S",
        "-u",
        "--block-signal",
        "--chdir",
        "--default-signal",
        "--ignore-signal",
        "--split-string",
        "--unset",
    }
    while option_index < len(tokens):
        option = tokens[option_index]
        if option == "--":
            return option_index + 1
        if _is_assignment(option):
            option_index += 1
            continue
        if not option.startswith("-") or option == "-":
            return option_index
        if option in options_with_argument:
            option_index += 1
        elif not (
            option in {"-0", "-i", "--ignore-environment", "--null"}
            or any(
                option.startswith(prefix)
                for prefix in (
                    "--block-signal=",
                    "--chdir=",
                    "--default-signal=",
                    "--ignore-signal=",
                    "--split-string=",
                    "--unset=",
                )
            )
        ):
            return None
        option_index += 1
    return None


def _is_command_start(tokens: list[str], index: int) -> bool:
    return index == 0 or tokens[index - 1] in {";", "&&", "||", "|", "&", "(", ")"}


def _command_executable_index(tokens: list[str], index: int) -> int:
    while index < len(tokens) and _is_assignment(tokens[index]):
        index += 1
    while index < len(tokens) and tokens[index] in {"command", "exec", "nohup"}:
        index += 1
    return index


def _is_python_adapter_invocation(
    tokens: list[str], interpreter_index: int, adapter_path: str
) -> bool:
    script_index = _python_script_index(tokens, interpreter_index)
    return (
        script_index is not None
        and script_index < len(tokens)
        and tokens[script_index] == adapter_path
    )


def _is_env_python_adapter_invocation(tokens: list[str], env_index: int, adapter_path: str) -> bool:
    interpreter_index = _env_command_index(tokens, env_index)
    return (
        interpreter_index is not None
        and interpreter_index < len(tokens)
        and _is_python_interpreter(tokens[interpreter_index])
        and _is_python_adapter_invocation(tokens, interpreter_index, adapter_path)
    )


def _references_managed_adapter(entry: dict[str, object], adapter: Path) -> bool:
    commands = entry["hooks"]
    assert isinstance(commands, list)
    adapter_path = str(adapter)
    for command in commands:
        if not isinstance(command, dict) or not isinstance(command.get("command"), str):
            continue
        tokens = _command_tokens(command["command"], adapter)
        if tokens is None:
            continue
        for command_index, _ in enumerate(tokens):
            if not _is_command_start(tokens, command_index):
                continue
            index = _command_executable_index(tokens, command_index)
            if index >= len(tokens):
                continue
            executable = tokens[index]
            if executable == adapter_path:
                return True
            if _is_python_interpreter(executable) and _is_python_adapter_invocation(
                tokens, index, adapter_path
            ):
                return True
            if executable == "/usr/bin/env" and _is_env_python_adapter_invocation(
                tokens, index, adapter_path
            ):
                return True
    return False


def _merged_hooks(payload: dict[str, object], adapter: Path) -> bytes:
    hooks = payload.setdefault("hooks", {})
    assert isinstance(hooks, dict)
    owned = {
        "SessionStart": _owned_entry(_START_MATCHER, adapter),
        "SessionEnd": _owned_entry(_END_MATCHER, adapter),
    }
    for event, entries in hooks.items():
        assert isinstance(entries, list)
        if any(
            _references_managed_adapter(entry, adapter)
            and (event not in owned or not _is_owned(entry, owned[event]))
            for entry in entries
        ):
            raise InstallError("existing managed Codex hook entries are ambiguous")
    for event, expected in owned.items():
        entries = hooks.get(event, [])
        assert isinstance(entries, list)
        if sum(_is_owned(entry, expected) for entry in entries) > 1:
            raise InstallError("existing managed Codex hook entries are ambiguous")
        hooks[event] = [entry for entry in entries if not _is_owned(entry, expected)] + [expected]
    return (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _legacy_proxy_paths(managed_dir: Path) -> tuple[Path, Path]:
    return (
        managed_dir / _LEGACY_ROOT_PROXY,
        managed_dir / "adapters/codex-app-server" / _LEGACY_NESTED_PROXY,
    )


def _legacy_plist_path(home: Path) -> Path:
    return home / "Library/LaunchAgents" / f"{_LABEL}.plist"


def _validate_legacy_plist(home: Path, legacy_proxies: tuple[Path, Path]) -> bool:
    path = _legacy_plist_path(home)
    if not path.exists():
        return False
    try:
        payload = plistlib.loads(path.read_bytes())
    except (OSError, plistlib.InvalidFileException) as error:
        raise InstallError("legacy Codex app-server LaunchAgent is invalid") from error
    expected = {
        "Label": _LABEL,
        "ProcessType": "Background",
        "ProgramArguments": ["/bin/launchctl", "setenv", "CODEX_CLI_PATH"],
        "RunAtLoad": True,
    }
    if not isinstance(payload, dict) or not any(
        payload
        == {
            **expected,
            "ProgramArguments": [*expected["ProgramArguments"], str(proxy)],
        }
        for proxy in legacy_proxies
    ):
        raise InstallError("legacy Codex app-server LaunchAgent is not installer-owned")
    return True


def _is_control_free(value: str) -> bool:
    return not any(unicodedata.category(character) == "Cc" for character in value)


def _validate_legacy_thread_state(home: Path) -> None:
    thread_state = home / _LEGACY_THREAD_STATE
    if not thread_state.exists():
        return
    if thread_state.is_symlink() or not thread_state.is_dir():
        raise InstallError("legacy Codex app-server thread state is not installer-owned")
    try:
        entries = tuple(thread_state.iterdir())
    except OSError as error:
        raise InstallError("legacy Codex app-server thread state is invalid") from error
    for entry in entries:
        if entry.name == ".state.lock":
            if entry.is_symlink() or not entry.is_file() or entry.stat().st_size != 0:
                raise InstallError("legacy Codex app-server thread state is not installer-owned")
            continue
        if entry.is_symlink() or not entry.is_file() or entry.suffix != ".json":
            raise InstallError("legacy Codex app-server thread state is not installer-owned")
        try:
            payload = json.loads(
                entry.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
            )
        except (OSError, json.JSONDecodeError) as error:
            raise InstallError("legacy Codex app-server thread state is invalid") from error
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema_version", "thread_id", "workspace"}
            or type(payload["schema_version"]) is not int
            or payload["schema_version"] != 1
            or not isinstance(payload["thread_id"], str)
            or not payload["thread_id"]
            or not _is_control_free(payload["thread_id"])
            or not isinstance(payload["workspace"], str)
            or not Path(payload["workspace"]).is_absolute()
            or not Path(payload["workspace"]).is_dir()
            or entry.stem != hashlib.sha256(payload["thread_id"].encode("utf-8")).hexdigest()
        ):
            raise InstallError("legacy Codex app-server thread state is not installer-owned")


def _require_launchctl(launchctl: Path) -> None:
    if not launchctl.is_file() or not os.access(launchctl, os.X_OK):
        raise InstallError("launchctl is required to deactivate the legacy integration")


def _deactivate_legacy(
    launchctl: Path, legacy_proxies: tuple[Path, Path], owns_plist: bool
) -> None:
    domain = f"gui/{os.getuid()}"
    try:
        service = subprocess.run(
            [str(launchctl), "print", f"{domain}/{_LABEL}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if service.returncode == 0:
            if not owns_plist:
                raise InstallError("active legacy Codex app-server service is not installer-owned")
            subprocess.run(
                [str(launchctl), "bootout", f"{domain}/{_LABEL}"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        current = subprocess.run(
            [str(launchctl), "getenv", "CODEX_CLI_PATH"],
            check=False,
            capture_output=True,
            text=True,
        )
        if current.returncode == 0 and current.stdout.strip() in {
            str(path) for path in legacy_proxies
        }:
            subprocess.run(
                [str(launchctl), "unsetenv", "CODEX_CLI_PATH"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except (OSError, subprocess.CalledProcessError) as error:
        raise InstallError("legacy Codex app-server teardown failed") from error


def _remove_legacy_artifacts(home: Path, managed_dir: Path) -> None:
    nested = managed_dir / "adapters/codex-app-server"
    for path in (
        _legacy_plist_path(home),
        managed_dir / _LEGACY_ROOT_PROXY,
        managed_dir / _LEGACY_REAL_CLI,
        managed_dir / _LEGACY_ROOT_ADAPTER,
        nested / _LEGACY_NESTED_PROXY,
        nested / _LEGACY_NESTED_ADAPTER,
        nested / _LEGACY_REAL_CLI,
    ):
        path.unlink(missing_ok=True)
    thread_state = home / _LEGACY_THREAD_STATE
    if thread_state.exists():
        shutil.rmtree(thread_state)


def install(
    *, home: Path, launchctl: Path = Path("/bin/launchctl"), activate: bool = True
) -> tuple[Path, Path]:
    source_dir = Path(__file__).resolve().parent
    scripts_dir = source_dir.parent.parent
    managed_dir = home / _MANAGED_RUNTIME
    adapter = managed_dir / "adapters/codex-app-server/adapter.py"
    hooks_path = _codex_config_root(home) / "hooks.json"

    if activate:
        _require_launchctl(launchctl)
    hooks_payload = _load_hooks(hooks_path)
    merged_hooks = _merged_hooks(hooks_payload, adapter)
    if (
        not (scripts_dir / "session-context-runtime.sh").is_file()
        or not (source_dir / "adapter.py").is_file()
    ):
        raise InstallError("required managed hook source is unavailable")

    legacy_proxies = _legacy_proxy_paths(managed_dir)
    owns_plist = _validate_legacy_plist(home, legacy_proxies)
    _validate_legacy_thread_state(home)

    _atomic_install(
        scripts_dir / "session-context-runtime.sh",
        managed_dir / "session-context-runtime.sh",
        0o755,
    )
    _atomic_install(source_dir / "adapter.py", adapter, 0o755)
    if activate:
        _deactivate_legacy(launchctl, legacy_proxies, owns_plist)
    _remove_legacy_artifacts(home, managed_dir)
    _atomic_write(hooks_path, merged_hooks, 0o600)
    return adapter, hooks_path


def main() -> int:
    adapter, hooks = install(home=Path.home())
    print(f"installed managed adapter: {adapter}")
    print(f"updated Codex hooks: {hooks}")
    print("Approve the hook interactively when Codex prompts, then restart Codex Desktop.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (InstallError, OSError, subprocess.CalledProcessError) as error:
        print(f"install-codex-app-server: {error}", file=sys.stderr)
        raise SystemExit(os.EX_CONFIG) from error
