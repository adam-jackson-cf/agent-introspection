"""``agent-introspection doctor``: read-only checks of every onboarding step.

Each check reports ``pass``, ``warn``, ``fail``, ``off`` (an optional feature that is
not in use), or ``info``, with a ``fix`` for anything that is not a pass; ``passed`` is
false when any check fails. The checks run in order: the config; SigNoz (``facts
preflight``, folded into one check); the collector's ports and the hooks endpoint; one
block per enabled harness (``doctor_harness.py``); the record spool; the schedule;
the prompt-label key; the dashboard's ClickHouse URL; and, with ``--drift``, each
enabled producer's telemetry against its saved fingerprint.

An additional machine (``[machine] role = "additional"``) has no SigNoz: the checks
that need the host's SigNoz, ClickHouse, or scheduled sync report ``info`` with the
reason, and it checks instead that the host's collector accepts connections, that the
hooks endpoint names it, and that the spool-sync timer runs.

Nothing is written. ``--live`` sends one empty OTLP/HTTP logs request, which stores
nothing; without it, no request carries data. A secret is checked for presence only,
and a key command is never run. A check that raises is reported as a failure naming
the exception class, and the other checks still run.
"""

from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Final, cast
from urllib.parse import urlsplit

from agent_introspection import doctor_harness, facts, hooks, preflight, profiles, spool
from agent_introspection.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_OTLP_ENDPOINT,
    AppConfig,
    ConfigurationError,
    is_local_host,
    load_config,
)
from agent_introspection.doctor_harness import Check, check, guarded

HTTP_PORT: Final = profiles.OTLP_PORTS["http"]
GRPC_PORT: Final = profiles.OTLP_PORTS["grpc"]
CONNECT_TIMEOUT_SECONDS: Final = 2
HTTP_TIMEOUT_SECONDS: Final = 2
# A valid OTLP/HTTP logs request with no records: the collector accepts and stores nothing.
EMPTY_LOGS_REQUEST: Final = b'{"resourceLogs":[]}'
DASHBOARD_URL_ENV: Final = "INTROSPECTION_CLICKHOUSE_URL"
OPENROUTER_KEY_ENV: Final = "OPENROUTER_API_KEY"
_UNREACHABLE: Final = "ClickHouse is unreachable (see the signoz check)"
_NO_CLICKHOUSE: Final = "an additional machine has no ClickHouse; run doctor on the host"
_ON_THE_HOST: Final = "run `agent-introspection doctor` on the host"
# The checks an additional machine leaves to the host, with the reason.
_LEFT_TO_THE_HOST: Final = {
    "signoz": "SigNoz, its collector, and its ClickHouse run on the host",
    "schedule": "the host's scheduled `facts sync` loads every machine's records",
    "prompt_labels": "the host's `facts sync` labels prompts with its own key",
    "dashboard": "the dashboard reads the host's ClickHouse",
}
_SCHEDULE_FIX: Final = "agent-introspection facts schedule install"
_SPOOL_SYNC_UNITS: Final = (
    "install the onboarding skill's agent-introspection-spool-sync units, as its "
    "additional-machines workflow describes"
)
_LEGACY_FIX: Final = (
    "reinstall the CLI (`uv tool install --force --reinstall .` in the repo), then "
    "`agent-introspection facts schedule install` retires the legacy job"
)
_CLICKHOUSE_HTTP_FIX: Final = (
    "point [signoz] clickhouse_url at ClickHouse's HTTP interface on this machine: "
    "SigNoz does not publish port 8123 by default, so publish it on loopback with the "
    "opt-in Compose override in the onboarding skill's "
    "scripts/signoz/docker-compose.clickhouse-http.yaml, or use OrbStack's "
    "http://<clickhouse container>.orb.local:8123"
)
# No proxy: a loopback address must never be sent through one.
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

type Connect = Callable[[AppConfig], facts.SqlRunner]
type TcpProbe = Callable[[str, int], bool]
type PostProbe = Callable[[str, bytes], int]
type GetProbe = Callable[[str], tuple[int, bytes]]
type StatusProbe = Callable[[], dict[str, Any]]
# ``producers.drift_report``; None names its default fingerprint directory.
type DriftProbe = Callable[[facts.SqlRunner, Path | None, Sequence[str]], dict[str, Any]]


@dataclass(frozen=True)
class Options:
    """What to check: the config file, and the opt-in live and drift checks.

    ``inbox`` and ``fingerprints`` default to the record spool and the saved
    fingerprints under ``~/.local/share/agent-introspection``.
    """

    config_path: Path | None = None
    live: bool = False
    drift: bool = False
    inbox: Path | None = None
    fingerprints: Path | None = None


@dataclass(frozen=True)
class Probes:
    """Every way the doctor touches the machine, so tests can replace each one.

    ``cli_doctor.default_probes`` wires the real ones: ``facts.runner``, the seams
    below, ``schedule.schedule_status`` and ``schedule.spool_sync_status``,
    ``signoz_detect.detect``, and ``producers.drift_report``.
    """

    connect: Connect
    tcp_open: TcpProbe
    post_json: PostProbe
    http_get: GetProbe
    schedule_status: StatusProbe
    spool_sync_status: StatusProbe
    detect: StatusProbe
    drift_report: DriftProbe
    harness: doctor_harness.HarnessProbes


def tcp_open(host: str, port: int) -> bool:
    """Return whether something accepts a TCP connection on ``host:port``."""
    try:
        with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


def post_json(url: str, body: bytes) -> int:
    """POST a JSON body the way the spool sends records; return the HTTP status."""
    request = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code


def http_get(url: str) -> tuple[int, bytes]:
    """GET a local URL without a proxy; return the status and the start of the body."""
    try:
        with _LOCAL_OPENER.open(url, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return int(response.status), cast(bytes, response.read(64))
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, b""


def _service_name(item: object) -> str:
    return item if isinstance(item, str) else str(cast(Mapping[str, object], item)["service"])


def _address(url: str) -> tuple[str, int]:
    """Return a URL's host and port, the scheme's default port when it names none."""
    parts = urlsplit(url)
    return parts.hostname or "", parts.port or (443 if parts.scheme == "https" else 80)


def _not_on_this_machine(name: str) -> Check:
    """Report a check an additional machine leaves to the host, with the reason."""
    reason = _LEFT_TO_THE_HOST[name]
    return check(name, "info", f"not checked on an additional machine: {reason}", _ON_THE_HOST)


class _Doctor:
    """One doctor run: its checks share the loaded config and the ClickHouse runner."""

    def __init__(self, options: Options, probes: Probes) -> None:
        self.options = options
        self.probes = probes
        self.config = AppConfig()
        # Set once ClickHouse answers; the host signal for the schedule check.
        self.run: facts.SqlRunner | None = None
        self.enabled: tuple[str, ...] = ()

    @property
    def environ(self) -> Mapping[str, str]:
        return self.probes.harness.environ

    @property
    def no_run(self) -> str:
        """Why telemetry and drift go uncounted when no ClickHouse answered."""
        return _NO_CLICKHOUSE if self.config.machine.additional else _UNREACHABLE

    def checks(self) -> list[Check]:
        found = guarded("config", self.config_check)
        for name, produce in self._steps():
            found += guarded(name, produce)
        if self.options.drift:
            found += guarded("drift", self.drift_checks)
        return found

    def _steps(self) -> tuple[tuple[str, Callable[[], Check | list[Check]]], ...]:
        """Return the checks after ``config``: the host's, or an additional machine's."""
        if not self.config.machine.additional:
            return (
                ("signoz", self.signoz_check),
                ("collector", self.collector_checks),
                ("harnesses", self.harness_checks),
                ("spool", self.spool_check),
                ("schedule", self.schedule_checks),
                ("prompt_labels", self.labels_check),
                ("dashboard", self.dashboard_check),
            )
        return (
            ("signoz", partial(_not_on_this_machine, "signoz")),
            ("collector", self.collector_checks),
            ("harnesses", self.harness_checks),
            ("spool", self.spool_check),
            ("schedule", self.spool_sync_checks),
            ("prompt_labels", partial(_not_on_this_machine, "prompt_labels")),
            ("dashboard", partial(_not_on_this_machine, "dashboard")),
        )

    def _collector_base(self) -> str:
        """Return the OTLP base URL of the collector this machine's harnesses export to."""
        return (self.config.collector_endpoint or DEFAULT_OTLP_ENDPOINT).rstrip("/")

    # --- Config and SigNoz -----------------------------------------------------------

    def config_check(self) -> Check:
        path = self.options.config_path
        shown = path.expanduser().resolve(strict=False) if path is not None else DEFAULT_CONFIG_PATH
        try:
            self.config = load_config(path)
        except ConfigurationError as exc:
            # Configuration errors name keys and variables, never a secret value.
            return check(
                "config",
                "fail",
                f"{exc}; the checks below use the defaults",
                "fix the file; config.example.toml documents every key",
                path=str(shown),
            )
        if not shown.is_file():
            return check(
                "config",
                "info",
                f"no file at {shown}: the defaults are in use",
                "copy config.example.toml there to choose harnesses and endpoints",
                path=str(shown),
            )
        return check("config", "pass", f"loaded {shown}", path=str(shown))

    def signoz_check(self) -> Check:
        runners: list[facts.SqlRunner] = []

        def connect(config: AppConfig) -> facts.SqlRunner:
            runners.append(self.probes.connect(config))
            return runners[-1]

        result = preflight.report(lambda: self.config, connect)
        checks = cast(list[Check], result["checks"])
        version = next(
            (item["version"] for item in checks if "version" in item),
            None,
        )
        if version is not None and runners:
            self.run = runners[-1]
        failing = [item for item in checks if item["status"] == "fail"]
        names = [item["name"] for item in failing]
        if not failing:
            return check(
                "signoz",
                "pass",
                f"ClickHouse {version}; every facts preflight check passes",
            )
        if self.run is None:
            return check(
                "signoz",
                "fail",
                f"ClickHouse is unreachable: {failing[0]['detail']}",
                self._unreachable_fix(),
                failing=names,
            )
        return check(
            "signoz",
            "fail",
            f"failing facts preflight checks: {', '.join(names)}",
            "run `agent-introspection facts preflight` for each check's detail",
            failing=names,
        )

    def _unreachable_fix(self) -> str:
        try:
            present = bool(self.probes.detect().get("signoz_present"))
        except Exception as exc:
            return (
                f"`signoz detect` raised {type(exc).__name__}: check that SigNoz is running "
                "and that [signoz] in the config names it"
            )
        if present:
            return (
                "SigNoz is present but its ClickHouse HTTP interface does not answer: "
                + _CLICKHOUSE_HTTP_FIX
            )
        return (
            "SigNoz seems absent: only if the user asks for a new one, run the onboarding "
            "skill's guided script, scripts/signoz/install-signoz.sh, with --dry-run first "
            "(it refuses when SigNoz exists). An additional machine, which sends to "
            'the host\'s collector, has no SigNoz by design: set [machine] role = "additional" '
            "and export_endpoint (config.example.toml)"
        )

    # --- Collector -------------------------------------------------------------------

    def collector_checks(self) -> list[Check]:
        base = self._collector_base()
        if self.config.machine.additional:
            checks = [self._host_collector_check(base)]
        else:
            parts = urlsplit(base)
            host = parts.hostname or "127.0.0.1"
            port = parts.port or HTTP_PORT
            checks = [
                self._port_check("collector_http", host, port),
                self._port_check("collector_grpc", host, GRPC_PORT),
            ]
        checks.append(self.hooks_endpoint_check())
        if self.options.live:
            checks.append(self.live_check(self.config.otlp_logs_endpoint))
        return checks

    def _host_collector_check(self, base: str) -> Check:
        host, port = _address(base)
        if self.probes.tcp_open(host, port):
            return check(
                "host_collector", "pass", f"the host's collector {host}:{port} accepts connections"
            )
        return check(
            "host_collector",
            "fail",
            f"nothing accepts connections at the host's collector {host}:{port} "
            "([machine] export_endpoint)",
            "on the host, start SigNoz and publish its collector's OTLP ports beyond loopback "
            "(the onboarding skill's additional-machines workflow) and let them through its "
            "firewall; or correct [machine] export_endpoint",
        )

    def _port_check(self, name: str, host: str, port: int) -> Check:
        grpc = port == GRPC_PORT
        label = "OTLP/gRPC" if grpc else "OTLP/HTTP"
        if self.probes.tcp_open(host, port):
            return check(name, "pass", f"{label} {host}:{port} accepts connections")
        return check(
            name,
            "warn" if grpc else "fail",
            f"nothing accepts {label} connections on {host}:{port}",
            "start the SigNoz collector, or set [signoz] otlp_endpoint to where it listens",
        )

    def hooks_endpoint_check(self) -> Check:
        endpoint = self.config.otlp_logs_endpoint
        host, port = _address(endpoint)
        where = f"{host}:{port}"
        if not self.probes.tcp_open(host, port):
            return check(
                "hooks_endpoint",
                "fail",
                f"nothing accepts connections at the hooks endpoint ({where})",
                "start the collector it names, or correct [hooks] otlp_logs_endpoint",
            )
        base = self._collector_base()
        if self.config.machine.additional and not profiles.on_collector_host(endpoint, base):
            return check(
                "hooks_endpoint",
                "warn",
                f"{where} accepts connections but is not the host's collector "
                f"({_address(base)[0]}): records go to another SigNoz unless both name one machine",
                "remove [hooks] otlp_logs_endpoint from the config, so records go to "
                "[machine] export_endpoint's /v1/logs",
            )
        return check("hooks_endpoint", "pass", f"{where} accepts connections")

    def live_check(self, target: str) -> Check:
        try:
            status = self.probes.post_json(target, EMPTY_LOGS_REQUEST)
        except OSError as exc:
            return check(
                "collector_live",
                "fail",
                f"an empty OTLP/HTTP logs request got no answer ({type(exc).__name__})",
                "start the collector, or correct [hooks] otlp_logs_endpoint",
            )
        if 200 <= status < 300:
            return check(
                "collector_live",
                "pass",
                f"the collector accepted an empty OTLP/HTTP logs request (HTTP {status})",
            )
        return check(
            "collector_live",
            "fail",
            f"the collector answered HTTP {status} to an empty OTLP/HTTP logs request",
            "point [hooks] otlp_logs_endpoint at an OTLP/HTTP logs URL, http://<host>:4318/v1/logs",
        )

    # --- Harnesses -------------------------------------------------------------------

    def _telemetry(self) -> doctor_harness.Telemetry:
        if self.run is None:
            return doctor_harness.Telemetry(skipped=self.no_run)
        try:
            return doctor_harness.Telemetry(
                counts=doctor_harness.telemetry_counts(self.run, self.enabled)
            )
        except Exception as exc:
            return doctor_harness.Telemetry(error=type(exc).__name__)

    def harness_checks(self) -> list[Check]:
        registry = facts.load_registry()
        try:
            self.enabled = facts.enabled_harnesses(registry, self.config.harnesses_enabled)
        except ConfigurationError as exc:
            return [check("harnesses", "fail", str(exc), "correct [harnesses] enabled")]
        known = profiles.load_profiles(registry)
        chosen = {
            profile.key: profile
            for profile in (profiles.resolve(key, known) for key in self.enabled)
        }
        context = doctor_harness.HarnessContext(
            config=self.config,
            enabled=self.enabled,
            telemetry=self._telemetry(),
            probes=self.probes.harness,
            now=time.time(),
        )
        return [
            item
            for profile in chosen.values()
            for item in doctor_harness.harness_checks(profile, context)
        ]

    # --- Spool and schedule ----------------------------------------------------------

    def spool_check(self) -> Check:
        inbox = self.options.inbox or hooks.INBOX
        pending, rejected = spool.backlog(inbox), spool.rejected_count(inbox)
        last = str(spool.read_state(inbox)["last_error_class"])
        home = self.probes.harness.home
        fixes = []
        if rejected:
            fixes.append(
                f"the collector refused {rejected} record file(s), kept in "
                f"{doctor_harness.shown_path(inbox / 'rejected', home)}: fix the cause, then "
                f"move them back into {doctor_harness.shown_path(inbox, home)} to resend "
                "(the loaders deduplicate)"
            )
        if last == "endpoint_misconfigured":
            fixes.append(
                "the hooks endpoint answers as if it were not an OTLP/HTTP logs URL: "
                "point [hooks] otlp_logs_endpoint at http://<host>:4318/v1/logs"
            )
        return check(
            "spool",
            "warn" if fixes else "pass",
            f"{pending} pending, {rejected} rejected"
            + (f"; last send error {last}" if last else ""),
            "; ".join(fixes),
            pending=pending,
            rejected=rejected,
            last_error_class=last,
        )

    def schedule_checks(self) -> list[Check]:
        status = self.probes.schedule_status()
        checks = [self._schedule_check(status)]
        legacy = status.get("legacy")
        # When only the legacy job runs, the schedule check already says so.
        if legacy and checks[0]["status"] != "warn":
            checks.append(
                check(
                    "schedule_legacy",
                    "warn",
                    f"the legacy job {legacy['label']} is still installed or loaded",
                    _LEGACY_FIX,
                )
            )
        if status.get("scheduler") == "systemd":
            checks.append(self._linger_check(bool(status.get("linger"))))
        return checks

    def _schedule_check(self, status: Mapping[str, Any]) -> Check:
        job = f"{status.get('scheduler')} job {status.get('label') or status.get('unit')}"
        if status.get("installed") and status.get("loaded"):
            return check("schedule", "pass", f"{job} is installed and loaded")
        if self.run is None:
            return check(
                "schedule",
                "info",
                f"{job} is not running; this machine reaches no SigNoz ClickHouse, so it is "
                'not the host (a machine without SigNoz sets [machine] role = "additional" '
                "and runs `spool sync` from the onboarding skill's units)",
            )
        legacy = status.get("legacy") or {}
        if legacy.get("loaded"):
            return check(
                "schedule",
                "warn",
                f"{job} is not running; the legacy job {legacy['label']} still runs the sync",
                _LEGACY_FIX,
            )
        if status.get("installed"):
            return check("schedule", "fail", f"{job} is installed but not loaded", _SCHEDULE_FIX)
        return check(
            "schedule", "fail", f"{job} is not installed on this SigNoz host", _SCHEDULE_FIX
        )

    def spool_sync_checks(self) -> list[Check]:
        """Check an additional machine's schedule: its spool-sync timer, not the host's."""
        skipped = _not_on_this_machine("schedule")
        status = self.probes.spool_sync_status()
        if status.get("scheduler") != "systemd":
            return [
                skipped,
                check(
                    "spool_sync",
                    "info",
                    f"no spool-sync job is bundled for {status.get('platform')}: hook and "
                    "spool-status records wait until `agent-introspection spool sync` runs",
                    "run `agent-introspection spool sync` every minute from a scheduler you "
                    "set up (launchd on macOS)",
                ),
            ]
        unit = status.get("unit")
        if status.get("installed") and status.get("loaded"):
            timer = check("spool_sync", "pass", f"systemd timer {unit} is installed and active")
        elif status.get("installed"):
            timer = check(
                "spool_sync",
                "fail",
                f"systemd timer {unit} is installed but not active",
                f"systemctl --user enable --now {unit}",
            )
        else:
            timer = check(
                "spool_sync", "fail", f"systemd timer {unit} is not installed", _SPOOL_SYNC_UNITS
            )
        return [skipped, timer, self._linger_check(bool(status.get("linger")))]

    @staticmethod
    def _linger_check(linger: bool) -> Check:
        if linger:
            return check("schedule_linger", "pass", "lingering is on: the timer runs logged out")
        return check(
            "schedule_linger",
            "warn",
            "lingering is off: the timer stops when you log out",
            'loginctl enable-linger "$USER"',
        )

    # --- Labels and dashboard --------------------------------------------------------

    def labels_check(self) -> Check:
        if self.config.labels_openrouter_key_command is not None:
            return check(
                "prompt_labels", "pass", "[labels] openrouter_key_command is set (not run here)"
            )
        command_fix = "set [labels] openrouter_key_command so the scheduled sync finds the key"
        if self.environ.get(OPENROUTER_KEY_ENV):
            return check(
                "prompt_labels",
                "info",
                f"{OPENROUTER_KEY_ENV} is set in this shell; the scheduled sync does not see it",
                command_fix,
            )
        if self.probes.harness.which("omp"):
            return check(
                "prompt_labels",
                "info",
                "no key configured: labels fall back to omp's stored OpenRouter credential "
                "(not checked)",
                command_fix,
            )
        return check(
            "prompt_labels",
            "off",
            "no OpenRouter key: prompt labels are optional and off",
            f"to label prompts, set {OPENROUTER_KEY_ENV} or [labels] openrouter_key_command "
            "(a command that prints the key, such as a Keychain lookup)",
        )

    def _dashboard_url(self) -> tuple[str, str]:
        if self.environ.get(DASHBOARD_URL_ENV):
            return self.environ[DASHBOARD_URL_ENV], DASHBOARD_URL_ENV
        return self.config.signoz.clickhouse_url, "[signoz] clickhouse_url"

    def dashboard_check(self) -> Check:
        url, source = self._dashboard_url()
        if not is_local_host(urlsplit(url).hostname or ""):
            return check(
                "dashboard",
                "warn",
                f"the URL in {source} is not on this machine, so the dashboard refuses it",
                _CLICKHOUSE_HTTP_FIX,
            )
        try:
            status, body = self.probes.http_get(f"{url.rstrip('/')}/ping")
        except OSError as exc:
            return check(
                "dashboard",
                "warn",
                f"ClickHouse at {source} does not answer /ping ({type(exc).__name__})",
                _CLICKHOUSE_HTTP_FIX,
            )
        if status == 200 and body.strip() == b"Ok.":
            return check("dashboard", "pass", f"ClickHouse at {source} answers /ping")
        return check(
            "dashboard",
            "warn",
            f"ClickHouse at {source} answered /ping with HTTP {status}",
            _CLICKHOUSE_HTTP_FIX,
        )

    # --- Drift -----------------------------------------------------------------------

    def drift_checks(self) -> list[Check]:
        if self.run is None:
            return [check("drift", "info", f"not checked: {self.no_run}")]
        found = self.probes.drift_report(self.run, self.options.fingerprints, self.enabled)
        entries = cast(list[Mapping[str, Any]], found.get("services", []))
        if all(entry.get("status") == "no_baseline" for entry in entries):
            checks = [
                check(
                    "drift",
                    "info",
                    "no baseline: no fingerprint is saved",
                    "run `agent-introspection producers fingerprint --save`",
                )
            ]
        else:
            checks = [self._drift_check(entry) for entry in entries]
        checks += [
            check(
                "drift",
                "warn",
                f"{name} sends telemetry but is not a registered harness",
                f"explore it with `agent-introspection producers profile {name}`",
                service=name,
            )
            for name in map(_service_name, found.get("unregistered", []))
        ]
        unnamed = int(found.get("unnamed_rows", 0))
        if unnamed:
            checks.append(
                check(
                    "drift_unnamed",
                    "info",
                    f"{unnamed} rows have an empty service.name, so no harness or producer "
                    "can claim them",
                    "find when they arrive with `agent-introspection producers discover` (its "
                    "`(empty)` entry), then set OTEL_SERVICE_NAME on the producer that sends "
                    "them",
                    unnamed_rows=unnamed,
                )
            )
        return checks

    @staticmethod
    def _drift_check(entry: Mapping[str, Any]) -> Check:
        service = _service_name(entry)
        status = entry.get("status")
        save = f"`agent-introspection producers fingerprint --save {service}`"
        if status == "drifted":
            added = sum(map(len, cast(Mapping[str, list[str]], entry.get("added", {})).values()))
            removed = sum(
                map(len, cast(Mapping[str, list[str]], entry.get("removed", {})).values())
            )
            return check(
                "drift",
                "warn",
                f"{service} gained {added} and lost {removed} span, event, or attribute names "
                "since its saved fingerprint",
                f"see what changed with `agent-introspection producers profile {service}`, "
                f"then save a new baseline with {save}",
                service=service,
            )
        if status == "invalid_baseline":
            return check(
                "drift",
                "warn",
                f"{service}'s saved fingerprint is unreadable",
                f"save it again with {save}",
                service=service,
            )
        if status == "unchanged":
            return check(
                "drift", "pass", f"{service} matches its saved fingerprint", service=service
            )
        return check(
            "drift", "info", f"{service} has no saved fingerprint", f"run {save}", service=service
        )


def report(options: Options, probes: Probes) -> dict[str, Any]:
    """Run every check; ``passed`` is false when any check fails."""
    checks = _Doctor(options, probes).checks()
    summary = dict.fromkeys(doctor_harness.STATUSES, 0)
    for item in checks:
        summary[item["status"]] += 1
    return {"passed": summary["fail"] == 0, "summary": summary, "checks": checks}
