"""Per-harness ``doctor`` checks: binary, config files, export settings, hooks, telemetry.

``doctor`` runs one block of these checks per enabled harness profile (``profiles.py``;
the ``codex`` profile covers its three surfaces). A profile's config files resolve as
``harness configure`` resolves the files it writes (``profiles.config_paths``):
``first`` takes the first path whose variables are set, ``each`` every chosen or
existing root, and a resolved file that does not exist is reported missing.

Files are read as ``harness configure`` reads them (``config_writers.read_table``).
Only a profile's managed settings are put into a result, and their values are shown
without URL credentials, queries, or exporter headers. Other keys, secrets among
them, never appear.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any, Final, cast
from urllib.parse import urlsplit

from agent_introspection import config_writers, facts, preflight, profiles
from agent_introspection.config import DEFAULT_CONFIG_PATH, AppConfig
from agent_introspection.json_types import JsonMapping

STATUSES: Final = ("pass", "warn", "fail", "off", "info")
VERSION_TIMEOUT_SECONDS: Final = 5
# Where `harness configure` installs the shim and the omp plugin, under the home.
MANAGED_RUNTIME: Final = Path(".local/lib/agent-introspection/activity-hooks-v1")
SHIM: Final = "activity-shim.sh"
# Beside the shim: the config path the hooks read, written only for a non-default one.
CONFIG_POINTER: Final = "config-path"
OMP_PLUGIN_LINK: Final = Path(".omp/plugins/node_modules/agent-introspection-activity")
OMP_PLUGIN_FILES: Final = ("package.json", "activity.ts")
# The events `harness configure claude-code` registers, with their tool-name matchers
# (hook_install.py).
CLAUDE_HOOK_EVENTS: Final[Mapping[str, str | None]] = {
    "PreToolUse": "*",
    "PostToolUseFailure": "*",
    "UserPromptSubmit": None,
    "Stop": None,
    "StopFailure": None,
    "SubagentStart": None,
}
# Rows are counted over a day; the last row is looked for over a week.
LAST_SEEN_DAYS: Final = 7
RAISED_FIX: Final = (
    "the check could not run: fix the cause its error class names, then rerun doctor"
)
# The error of a resolved config file that does not exist yet.
MISSING: Final = "missing"
_RANK = {"pass": 0, "info": 1, "off": 2, "warn": 3, "fail": 4}
_DAY_SECONDS = 86_400
_VERSION = re.compile(r"\d+(?:\.\d+)+")
_SHOWN_CHARACTERS = 60
_TELEMETRY_SQL = (
    "SELECT source, service, countIf(ts > now() - INTERVAL 1 DAY) AS rows_24h, "
    "toUnixTimestamp(max(ts)) AS last_seen FROM ("
    "SELECT 'spans' AS source, serviceName AS service, toDateTime(timestamp) AS ts "
    "FROM {spans} WHERE serviceName IN ({services}) "
    "AND timestamp > now() - INTERVAL {days} DAY "
    "UNION ALL SELECT 'logs' AS source, resource.`service.name`::String AS service, "
    "toDateTime(intDiv(timestamp, 1000000000)) AS ts "
    "FROM {logs} WHERE resource.`service.name`::String IN ({services}) "
    "AND timestamp > toUInt64(toUnixTimestamp(now() - INTERVAL {days} DAY)) * 1000000000"
    ") GROUP BY source, service FORMAT JSONEachRow"
)

type Check = dict[str, Any]
type Which = Callable[[str], str | None]
type VersionProbe = Callable[[Sequence[str]], str]


def check(name: str, status: str, detail: str, fix: str = "", **extra: object) -> Check:
    """Return one doctor check; ``fix`` says what to do when it is not a pass."""
    return {"name": name, "status": status, "detail": detail, "fix": fix, **extra}


def guarded(name: str, produce: Callable[[], Check | list[Check]], **extra: object) -> list[Check]:
    """Run one check, adding ``extra`` to each result.

    An exception becomes a failing check that names only its class, so one check never
    stops the report and an error message never carries a value into it.
    """
    try:
        result = produce()
    except Exception as exc:
        return [check(name, "fail", f"the check raised {type(exc).__name__}", RAISED_FIX, **extra)]
    return [{**item, **extra} for item in (result if isinstance(result, list) else [result])]


def worst(statuses: Iterable[str]) -> str:
    """Return the most severe status (``pass`` for none)."""
    return max(statuses, key=_RANK.__getitem__, default="pass")


@dataclass(frozen=True)
class HarnessProbes:
    """How the harness checks find binaries, run them, and resolve paths."""

    which: Which
    version: VersionProbe
    environ: Mapping[str, str]
    home: Path


@dataclass(frozen=True)
class Telemetry:
    """Each enabled service's rows in the last day and last-seen time, or why not counted.

    ``counts`` maps a service to ``spans`` and ``logs`` (rows in 24 hours) and
    ``last_seen`` (Unix seconds, 0 when none in ``LAST_SEEN_DAYS``). ``skipped`` says
    why nothing was asked; ``error`` names the class of a failed query.
    """

    counts: Mapping[str, Mapping[str, int]] = field(default_factory=dict)
    skipped: str = ""
    error: str = ""


@dataclass(frozen=True)
class HarnessContext:
    """What every harness block shares: the config, enabled harnesses, and the probes."""

    config: AppConfig
    enabled: tuple[str, ...]
    telemetry: Telemetry
    probes: HarnessProbes
    now: float
    config_path: Path | None = None


@dataclass(frozen=True)
class LoadedFile:
    """One resolved config file, or the class of the error that kept it from being read.

    ``document`` is the whole file and ``table`` the settings table the profile manages
    (the same for ``table = ""``), both as ``config_writers.read_table`` reads them.
    """

    spec: profiles.ConfigFile
    path: Path
    document: JsonMapping = field(default_factory=dict)
    table: JsonMapping = field(default_factory=dict)
    error: str = ""


# --- Showing values ----------------------------------------------------------------


def shown_path(path: Path, home: Path) -> str:
    """Return ``path`` with the home directory written as ``~``."""
    try:
        return f"~/{path.relative_to(home).as_posix()}"
    except ValueError:
        return str(path)


def shell_path(path: Path, home: Path) -> str:
    """Return ``path`` quoted for a pasted shell command, the home directory as ``~``.

    The ``~/`` stays outside the quotes so the shell still expands it.
    """
    try:
        relative = path.relative_to(home).as_posix()
    except ValueError:
        return shlex.quote(str(path))
    return f"~/{shlex.quote(relative)}"


def shown_text(text: str) -> str:
    """Return a URL without credentials, query, or fragment; other text, shortened.

    ``doctor`` and ``harness configure`` show a managed setting's text through it.
    """
    parts = urlsplit(text)
    if parts.scheme and parts.hostname:
        try:
            port = f":{parts.port}" if parts.port is not None else ""
        except ValueError:
            port = ""
        host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
        return f"{parts.scheme}://{host}{port}{parts.path}"
    if len(text) <= _SHOWN_CHARACTERS:
        return text
    return text[: _SHOWN_CHARACTERS - 3] + "..."


def _shown_table(table: Mapping[str, object]) -> str:
    """Name an exporter table by kind and endpoint only, never its headers."""
    if len(table) == 1:
        kind, options = next(iter(table.items()))
        endpoint = options.get("endpoint") if isinstance(options, Mapping) else None
        if isinstance(endpoint, str):
            return f"{kind} {shown_text(endpoint)}"
    return f"a table of {', '.join(sorted(map(str, table)))}"


def shown_value(value: object) -> str:
    """Return a managed setting's value as safe to print."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return repr(shown_text(value))
    if isinstance(value, Mapping):
        return _shown_table(cast(Mapping[str, object], value))
    return type(value).__name__


def age(seconds: float) -> str:
    """Return a short age such as ``5 min``, ``3 h``, or ``2 d``."""
    minutes = int(max(seconds, 0) // 60)
    if minutes < 60:
        return f"{minutes} min"
    if minutes < 48 * 60:
        return f"{minutes // 60} h"
    return f"{minutes // (24 * 60)} d"


# --- Binary --------------------------------------------------------------------------


def _run_version(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=VERSION_TIMEOUT_SECONDS,
        check=False,
    )


def installed_version(argv: Sequence[str]) -> str:
    """Return the first dotted version number ``argv`` prints, or ``""`` for none."""
    completed = _run_version(argv)
    match = _VERSION.search(completed.stdout) or _VERSION.search(completed.stderr)
    return match.group(0) if match else ""


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def binary_check(profile: profiles.Profile, probes: HarnessProbes) -> Check:
    """Find the binary on PATH and compare its version with the verified ones."""
    found = probes.which(profile.binary)
    if found is None:
        return check(
            "binary",
            "warn",
            f"{profile.binary} is not on PATH",
            f"install {profile.title}, or put {profile.binary} on PATH",
        )
    try:
        version = probes.version([found, *profile.version_args])
    except (OSError, subprocess.SubprocessError) as exc:
        version, failure = "", f" ({type(exc).__name__})"
    else:
        failure = ""
    verified = max(profile.verified_versions, key=_version_key)
    if not version:
        return check(
            "binary",
            "warn",
            f"{profile.binary} printed no version{failure}",
            f"check that `{profile.binary} {' '.join(profile.version_args)}` runs",
        )
    if version in profile.verified_versions:
        return check("binary", "pass", f"{profile.binary} {version} (verified)", version=version)
    if _version_key(version) > _version_key(verified):
        return check(
            "binary",
            "info",
            f"{profile.binary} {version} is newer than the verified {verified}",
            "run `agent-introspection doctor --drift` to check its telemetry still matches "
            "the saved fingerprints",
            version=version,
        )
    return check(
        "binary",
        "info",
        f"{profile.binary} {version} is not a verified version ({verified} is)",
        f"update {profile.title} if its signals are missing",
        version=version,
    )


# --- Config files --------------------------------------------------------------------


def _unique(paths: Iterable[Path]) -> list[Path]:
    seen: dict[Path, Path] = {}
    for path in paths:
        seen.setdefault(path.resolve(), path)
    return list(seen.values())


def config_paths(spec: profiles.ConfigFile, probes: HarnessProbes) -> list[Path]:
    """Return the files ``spec`` names, as ``harness configure`` resolves the ones it writes."""
    return _unique(profiles.config_paths(spec, probes.environ, probes.home, []))


def load_file(spec: profiles.ConfigFile, path: Path) -> LoadedFile:
    """Read one config file as ``harness configure`` does; a failure keeps its error class.

    A file that does not exist has the error ``MISSING``.
    """
    if not path.exists() and not path.is_symlink():
        return LoadedFile(spec, path, error=MISSING)
    try:
        text = path.read_text(encoding="utf-8")
        document = config_writers.read_table(text, spec.format, "")
        table = config_writers.read_table(text, spec.format, spec.table)
    except (OSError, ValueError) as exc:
        return LoadedFile(spec, path, error=type(exc).__name__)
    return LoadedFile(spec, path, document, table)


def load_files(profile: profiles.Profile, context: HarnessContext) -> list[LoadedFile]:
    """Resolve and read every config file the profile names."""
    return [
        load_file(spec, path)
        for spec in profile.config_files
        for path in config_paths(spec, context.probes)
    ]


def files_check(
    profile: profiles.Profile, files: Sequence[LoadedFile], probes: HarnessProbes
) -> Check:
    """Check every config file the profile names exists and can be read."""
    found = [shown_path(item.path, probes.home) for item in files if not item.error]
    broken = [
        f"{shown_path(item.path, probes.home)} ({item.error})"
        for item in files
        if item.error and item.error != MISSING
    ]
    absent = [
        *(f"at {shown_path(item.path, probes.home)}" for item in files if item.error == MISSING),
        *(
            f"for {spec.id} ({' or '.join(spec.paths)})"
            for spec in profile.config_files
            if not any(item.spec.id == spec.id for item in files)
        ),
    ]
    if broken:
        return check(
            "config_files",
            "fail",
            f"cannot be read: {'; '.join(broken)}",
            "fix the file (its syntax, or a settings key that is not a table), then rerun doctor",
            files=found,
        )
    if absent:
        return check(
            "config_files",
            "fail",
            f"no file {'; '.join(absent)}",
            f"agent-introspection harness configure {profile.key}",
            files=found,
        )
    return check("config_files", "pass", f"read {', '.join(found)}", files=found)


# --- Settings ------------------------------------------------------------------------


def _result(key: str, status: str, detail: str) -> dict[str, str]:
    return {"key": key, "status": status, "detail": detail}


def setting_result(
    profile: profiles.Profile,
    setting: profiles.Setting,
    table: JsonMapping,
    context: Mapping[str, str],
) -> dict[str, str]:
    """Judge one managed setting against its rendered value and transport equivalence.

    A missing or different service name only warns, because producers name their own
    service by default; any other export setting turns on or routes export, so it fails.
    """
    expected = setting.render(context)
    actual = table.get(setting.key)
    if setting.key in table and actual == expected:
        return _result(setting.key, "pass", "matches")
    transport = profiles.equivalent_transport(profile, setting, table, context)
    if transport is not None:
        return _result(setting.key, "pass", f"satisfied ({transport})")
    status = "warn" if setting.value == "{service_name}" else "fail"
    if setting.key not in table:
        return _result(setting.key, status, "missing")
    return _result(
        setting.key, status, f"is {shown_value(actual)}, expected {shown_value(expected)}"
    )


def _settings_detail(results: Sequence[Mapping[str, str]]) -> str:
    """Summarise setting results: ``6 match; satisfied (grpc): A, B; missing: C``."""
    groups: dict[str, list[str]] = {}
    for result in results:
        detail = result["detail"]
        if detail.startswith("is "):
            groups.setdefault("different", []).append(f"{result['key']} ({detail})")
        else:
            groups.setdefault(detail, []).append(result["key"])
    matched = groups.pop("matches", [])
    parts = [f"{len(matched)} match"] if matched else []
    parts += [f"{label}: {', '.join(keys)}" for label, keys in groups.items()]
    return "; ".join(parts) or "no managed export settings"


def _configure_fix(profile: profiles.Profile, item: LoadedFile, probes: HarnessProbes) -> str:
    root = (
        f" --root {shell_path(item.path.parent, probes.home)}" if item.spec.roots == "each" else ""
    )
    return f"agent-introspection harness configure {profile.key}{root}"


def export_check(profile: profiles.Profile, item: LoadedFile, context: HarnessContext) -> Check:
    """Check one config file's export settings."""
    rendering = profiles.export_context(profile, context.config.collector_endpoint)
    results = [
        setting_result(profile, setting, item.table, rendering)
        for setting in profile.settings
        if setting.file == item.spec.id and setting.group == "export"
    ]
    status = worst(result["status"] for result in results)
    path = shown_path(item.path, context.probes.home)
    # A file kept in each root (a Codex home) is named, so its checks tell apart.
    where = f"{path}: " if item.spec.roots == "each" else ""
    return check(
        "export_settings",
        status,
        where + _settings_detail(results),
        "" if status == "pass" else _configure_fix(profile, item, context.probes),
        path=path,
        settings=results,
    )


def export_checks(
    profile: profiles.Profile, files: Sequence[LoadedFile], context: HarnessContext
) -> list[Check]:
    """Check the export settings of every config file that parsed."""
    return [export_check(profile, item, context) for item in files if not item.error]


@dataclass(frozen=True)
class _PromptSetting:
    """One prompt-export setting in one config file, against the profile's value."""

    item: LoadedFile
    setting: profiles.Setting
    wanted: profiles.SettingValue

    @property
    def state(self) -> str:
        """``on`` (the profile's value), ``unset``, or ``other`` (a value set by hand)."""
        if self.setting.key not in self.item.table:
            return "unset"
        return "on" if self.item.table[self.setting.key] == self.wanted else "other"

    def describe(self, home: Path) -> str:
        where = f"{self.setting.key} in {shown_path(self.item.path, home)}"
        if self.state == "unset":
            return f"{where} not set"
        actual = shown_value(self.item.table[self.setting.key])
        return f"{where} is {actual}, where the profile writes {shown_value(self.wanted)}"


@dataclass(frozen=True)
class _PromptFile:
    """One config file's prompt-export settings, in profile order.

    The first is the file's switch: prompt text is exported only while it holds the
    profile's value. Any others qualify what the switch exports (Claude Code's keeps
    assistant responses redacted); one left unset follows the switch.
    """

    item: LoadedFile
    settings: tuple[_PromptSetting, ...]

    @property
    def exporting(self) -> bool:
        """Whether the switch is on, so the harness exports prompt text from this file."""
        return self.settings[0].state == "on"

    @property
    def differing(self) -> list[_PromptSetting]:
        """The settings not at the profile's value."""
        return [row for row in self.settings if row.state != "on"]


def _prompt_files(
    profile: profiles.Profile, files: Sequence[LoadedFile], rendering: Mapping[str, str]
) -> list[_PromptFile]:
    """Return each readable file's prompt-export settings; files with none are left out."""
    found = (
        _PromptFile(
            item,
            tuple(
                _PromptSetting(item, setting, setting.render(rendering))
                for setting in profile.settings
                if setting.group == "prompt_export" and setting.file == item.spec.id
            ),
        )
        for item in files
        if not item.error
    )
    return [prompt_file for prompt_file in found if prompt_file.settings]


def _prompt_state(prompt_files: Sequence[_PromptFile]) -> str:
    """Return ``on``, ``off``, ``partly``, or ``by_hand`` for a harness's files together.

    ``partly``: some files export and others do not (one Codex home of several), or an
    exporting file leaves a qualifying setting unset. ``by_hand``: every file exports
    and a qualifying setting holds a value set by hand.
    """
    exporting = [prompt_file for prompt_file in prompt_files if prompt_file.exporting]
    if not exporting:
        return "off"
    if len(exporting) < len(prompt_files) or any(
        row.state == "unset" for prompt_file in exporting for row in prompt_file.settings
    ):
        return "partly"
    return "by_hand" if any(prompt_file.differing for prompt_file in exporting) else "on"


def _prompt_configure(
    profile: profiles.Profile, prompt_files: Sequence[_PromptFile], home: Path
) -> str:
    """Return the ``--prompt-export`` command for the files that differ.

    A file kept in each root (a Codex home) is named with ``--root``, as
    ``export_check`` does, so the command writes the files that differ.
    """
    roots = "".join(
        f" --root {shell_path(prompt_file.item.path.parent, home)}"
        for prompt_file in prompt_files
        if prompt_file.differing and prompt_file.item.spec.roots == "each"
    )
    return f"`agent-introspection harness configure {profile.key} --prompt-export{roots}`"


def prompt_check(
    profile: profiles.Profile, files: Sequence[LoadedFile], context: HarnessContext
) -> list[Check]:
    """Report whether prompt export is on; off is a choice, never a failure.

    A file exports prompt text only while its switch (its first prompt-export setting)
    holds the profile's value, so a file whose switch is unset or set otherwise is
    ``off`` whatever its other settings hold. A harness some of whose files export and
    others do not warns, naming the files that export. So does an exporting file that
    leaves a qualifying setting unset (Claude Code's switch alone also exports
    assistant responses): ``harness configure`` writes all of them or none. An
    exporting file whose qualifying settings hold a value set by hand (such as
    ``OTEL_LOG_ASSISTANT_RESPONSES=1``) is ``info``: a deliberate choice, reported with
    the value and what ``harness configure --prompt-export`` would write over it.
    """
    rendering = profiles.export_context(profile, context.config.collector_endpoint)
    prompt_files = _prompt_files(profile, files, rendering)
    if not prompt_files:
        return []
    state = _prompt_state(prompt_files)
    if state == "on":
        return [check("prompt_export", "pass", "on: prompt text is exported for prompt labels")]
    home = context.probes.home
    differing = [row for prompt_file in prompt_files for row in prompt_file.differing]
    detail = "; ".join(row.describe(home) for row in differing)
    configure = _prompt_configure(profile, prompt_files, home)
    writes = ", ".join(
        dict.fromkeys(f"{row.setting.key} to {shown_value(row.wanted)}" for row in differing)
    )
    if state == "partly":
        exporting = [prompt_file for prompt_file in prompt_files if prompt_file.exporting]
        paths = [shown_path(prompt_file.item.path, home) for prompt_file in exporting]
        where = f"on in {', '.join(paths)}; " if len(exporting) < len(prompt_files) else ""
        switches = ", ".join(
            f"{prompt_file.settings[0].setting.key} from {path}"
            for prompt_file, path in zip(exporting, paths, strict=True)
        )
        return [
            check(
                "prompt_export",
                "warn",
                f"partly on: {where}{detail}",
                f"{configure} sets {writes}; or remove {switches} to keep prompt export off",
            )
        ]
    if state == "by_hand":
        return [
            check(
                "prompt_export",
                "info",
                f"set by hand: {detail}",
                f"keep a value you set on purpose; {configure} would set {writes}",
            )
        ]
    return [
        check(
            "prompt_export",
            "off",
            f"off: {detail}",
            f"optional: {configure} exports prompt text so `facts sync` can label prompts; "
            "the facts store keeps only the labels",
        )
    ]


# --- Activity hooks ------------------------------------------------------------------


def claude_hook_command(shim: Path, event: str) -> str:
    """Return the command the Claude Code installer writes for ``event``."""
    return f"/bin/sh {shlex.quote(str(shim))} claude-code {event}"


def _entry_runs(entry: object, matcher: str | None, command: str) -> bool:
    if not isinstance(entry, Mapping):
        return False
    if matcher is not None and entry.get("matcher") != matcher:
        return False
    hooks = entry.get("hooks")
    return isinstance(hooks, list) and any(
        isinstance(hook, Mapping) and hook.get("command") == command for hook in hooks
    )


def _runs_shim(entries: object, matcher: str | None, command: str) -> bool:
    return isinstance(entries, list) and any(
        _entry_runs(entry, matcher, command) for entry in entries
    )


def claude_hooks_check(files: Sequence[LoadedFile], home: Path) -> Check:
    """Check settings.json runs the managed shim for each activity event."""
    fix = "`agent-introspection harness configure claude-code` (with --dry-run first)"
    settings = next((item for item in files if item.spec.format == "json" and not item.error), None)
    if settings is None:
        return check("activity_hooks", "fail", "no readable settings.json to hold the hooks", fix)
    shim = home / MANAGED_RUNTIME / SHIM
    hooks = settings.document.get("hooks")
    table = cast(Mapping[str, object], hooks) if isinstance(hooks, Mapping) else {}
    missing = [
        event
        for event, matcher in CLAUDE_HOOK_EVENTS.items()
        if not _runs_shim(table.get(event), matcher, claude_hook_command(shim, event))
    ]
    if missing:
        return check(
            "activity_hooks",
            "fail",
            f"{len(missing)} of {len(CLAUDE_HOOK_EVENTS)} events do not run the managed shim: "
            + ", ".join(missing),
            fix,
        )
    return check(
        "activity_hooks", "pass", f"all {len(CLAUDE_HOOK_EVENTS)} events run the managed shim"
    )


def omp_plugin_check(home: Path) -> Check:
    """Check omp's plugin link resolves to the managed plugin directory."""
    managed = home / MANAGED_RUNTIME / "omp"
    fix = "`agent-introspection harness configure omp` (with --dry-run first)"
    link = home / OMP_PLUGIN_LINK
    if not link.exists():
        problem = "is a broken link" if link.is_symlink() else "does not exist"
        return check("activity_hooks", "fail", f"{shown_path(link, home)} {problem}", fix)
    if link.resolve() != managed.resolve():
        return check(
            "activity_hooks",
            "fail",
            f"{shown_path(link, home)} does not resolve to {shown_path(managed, home)}",
            fix,
        )
    absent = [name for name in OMP_PLUGIN_FILES if not (managed / name).is_file()]
    if absent:
        return check("activity_hooks", "fail", f"the plugin lacks {', '.join(absent)}", fix)
    shipped = files("agent_introspection").joinpath("hook_runtime", "omp")
    stale = [
        name
        for name in OMP_PLUGIN_FILES
        if (managed / name).read_text("utf-8") != shipped.joinpath(name).read_text("utf-8")
    ]
    if stale:
        return check(
            "activity_hooks",
            "warn",
            f"the plugin is out of date: {', '.join(stale)} differ from what this CLI ships",
            "`agent-introspection harness configure omp` updates it",
        )
    return check("activity_hooks", "pass", f"the omp plugin links {shown_path(managed, home)}")


def pointed_config(config_path: Path | None) -> str | None:
    """Return the config path the hooks must be pointed at; None for the default file."""
    if config_path is None:
        return None
    selected = config_path.expanduser().resolve(strict=False)
    return None if selected == DEFAULT_CONFIG_PATH.resolve(strict=False) else str(selected)


def pointer_check(home: Path, profile_key: str, config_path: Path | None) -> Check:
    """Check the hooks read the config file doctor checked."""
    pointer = home / MANAGED_RUNTIME / CONFIG_POINTER
    read = pointer.read_text("utf-8").strip() if pointer.is_file() else None
    expected = pointed_config(config_path)
    if read == expected:
        shown = read or f"the default {shown_path(DEFAULT_CONFIG_PATH, home)}"
        return check("hook_config", "pass", f"the hooks read {shown}")
    selected = f" --config {expected}" if expected else ""
    return check(
        "hook_config",
        "warn",
        f"the hooks read {read or 'the default config'}, not the one doctor checked",
        f"`agent-introspection{selected} harness configure {profile_key}` points them at it",
    )


def shim_check(home: Path, profile_key: str) -> Check:
    """Check the managed shim exists, is executable, and is the one this CLI ships."""
    shim = home / MANAGED_RUNTIME / SHIM
    fix = f"`agent-introspection harness configure {profile_key}` installs it"
    if not shim.is_file():
        return check("activity_shim", "fail", f"{shown_path(shim, home)} does not exist", fix)
    if not os.access(shim, os.X_OK):
        return check("activity_shim", "fail", f"{shown_path(shim, home)} is not executable", fix)
    shipped = files("agent_introspection").joinpath("hook_runtime", SHIM).read_text("utf-8")
    if shim.read_text("utf-8") != shipped:
        return check(
            "activity_shim",
            "warn",
            f"{shown_path(shim, home)} differs from the shim this CLI ships",
            f"`agent-introspection harness configure {profile_key}` updates it",
        )
    return check("activity_shim", "pass", f"{shown_path(shim, home)} is executable and current")


def hook_checks(
    profile: profiles.Profile,
    files: Sequence[LoadedFile],
    home: Path,
    config_path: Path | None = None,
) -> list[Check]:
    """Check the activity hooks of a profile with a hook adapter."""
    runtime = [shim_check(home, profile.key), pointer_check(home, profile.key, config_path)]
    if profile.hook_adapter == "claude-code":
        return [claude_hooks_check(files, home), *runtime]
    if profile.hook_adapter == "omp":
        return [omp_plugin_check(home), *runtime]
    return []


# --- Telemetry -----------------------------------------------------------------------


def telemetry_counts(run: facts.SqlRunner, services: Sequence[str]) -> dict[str, dict[str, int]]:
    """Count each service's spans and logs in the last day and find its last row (one query)."""
    counts = {service: {"spans": 0, "logs": 0, "last_seen": 0} for service in services}
    if not services:
        return counts
    output = run(
        _TELEMETRY_SQL.format(
            spans=".".join(preflight.SPAN_TABLE),
            logs=".".join(preflight.LOG_TABLE),
            services=", ".join(map(facts.sql_string, services)),
            days=LAST_SEEN_DAYS,
        )
    )
    for row in map(json.loads, filter(None, output.splitlines())):
        entry = counts.get(str(row["service"]))
        if entry is not None and row["source"] in ("spans", "logs"):
            entry[str(row["source"])] = int(row["rows_24h"])
            entry["last_seen"] = max(entry["last_seen"], int(row["last_seen"]))
    return counts


def telemetry_check(profile: profiles.Profile, context: HarnessContext) -> Check:
    """Report the profile's rows in the last day; none means idle or export off."""
    telemetry = context.telemetry
    if telemetry.skipped:
        return check("telemetry", "info", f"not counted: {telemetry.skipped}")
    if telemetry.error:
        return check(
            "telemetry",
            "fail",
            f"the telemetry query raised {telemetry.error}",
            "see the signoz check for ClickHouse access",
        )
    empty = {"spans": 0, "logs": 0, "last_seen": 0}
    counts = {
        service: telemetry.counts.get(service, empty)
        for service in profile.registry_keys
        if service in context.enabled
    }
    rows = sum(entry["spans"] + entry["logs"] for entry in counts.values())
    last = max((entry["last_seen"] for entry in counts.values()), default=0)
    seen = f"last seen {age(context.now - last)} ago" if last else f"none in {LAST_SEEN_DAYS} days"
    per_service = ", ".join(
        f"{service} {entry['spans']} spans {entry['logs']} logs"
        for service, entry in counts.items()
    )
    if rows:
        return check("telemetry", "pass", f"24 h: {per_service}; {seen}", services=counts)
    return check(
        "telemetry",
        "warn",
        f"no rows in 24 h; {seen}",
        f"{profile.title} is idle or its export is off: check export_settings, use "
        f"{profile.title} once, then rerun doctor",
        services=counts,
    )


# --- The block -----------------------------------------------------------------------


def harness_checks(profile: profiles.Profile, context: HarnessContext) -> list[Check]:
    """Run every check for one enabled harness profile, each isolated from the others."""
    files: list[LoadedFile] = []

    def config_files() -> Check:
        files.extend(load_files(profile, context))
        return files_check(profile, files, context.probes)

    home = context.probes.home
    return [
        *guarded("binary", lambda: binary_check(profile, context.probes), harness=profile.key),
        *guarded("config_files", config_files, harness=profile.key),
        *guarded(
            "export_settings", lambda: export_checks(profile, files, context), harness=profile.key
        ),
        *guarded(
            "prompt_export", lambda: prompt_check(profile, files, context), harness=profile.key
        ),
        *guarded(
            "activity_hooks",
            lambda: hook_checks(profile, files, home, context.config_path),
            harness=profile.key,
        ),
        *guarded("telemetry", lambda: telemetry_check(profile, context), harness=profile.key),
    ]
