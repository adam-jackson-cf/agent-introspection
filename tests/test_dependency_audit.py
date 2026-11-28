from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPTS = Path(__file__).parents[1] / "scripts"


def _load(name: str) -> ModuleType:
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), _SCRIPTS / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


audit = _load("check-dependency-audit")
_TODAY = dt.date(2026, 11, 27)
_ZOD = (
    '{"zod":[{"id":1243066,"url":"https://github.com/advisories/GHSA-mrhx-c7xc-m3w5",'
    '"title":"Zod can exhaust memory","severity":"high","vulnerable_versions":"<4.6.6",'
    '"cwe":["CWE-770"]}]}'
)


def _policy(tmp_path: Path, exceptions: list[dict[str, str]]) -> object:
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"excluded": [], "exceptions": exceptions}), encoding="utf-8")
    return audit.load_policy(path)


def _advisory(fixes: tuple[str, ...]) -> object:
    return audit.Advisory("app/bun.lock", "npm", "zod", "3.25.76", "GHSA-x", (), fixes)


def _entry(**extra: str) -> dict[str, str]:
    return {
        "advisory": "GHSA-x",
        "package": "zod",
        "version": "3.25.76",
        "lock": "app/bun.lock",
        "reason": "no fix published",
        "exposure": "dev only",
        "expires": "2026-12-31",
        **extra,
    }


def test_bun_advisory_line_follows_the_grammar() -> None:
    found = audit.parse_bun_audit("app/bun.lock", "bun audit v1.3.0\n" + _ZOD, {"zod": {"3.25.76"}})

    assert [item.render() for item in found] == [
        "SECURITY.dependency-audit lock=app/bun.lock ecosystem=npm package=zod "
        "version=3.25.76 advisory=GHSA-mrhx-c7xc-m3w5 aliases= fix=4.6.6"
    ]


def test_bun_malware_advisory_carries_a_mal_alias() -> None:
    text = _ZOD.replace('"CWE-770"', '"CWE-506"')

    found = audit.parse_bun_audit("app/bun.lock", text, {"zod": {"3.25.76"}})

    assert found[0].aliases == ("MAL-GHSA-mrhx-c7xc-m3w5",)


def test_bun_output_that_is_not_json_is_an_auditor_failure() -> None:
    with pytest.raises(audit.AuditError, match="not JSON"):
        audit.parse_bun_audit("app/bun.lock", "error: failed", {})


def test_bun_advisory_matching_no_locked_version_is_untrusted() -> None:
    with pytest.raises(audit.AuditError, match="matches no locked version"):
        audit.parse_bun_audit("app/bun.lock", _ZOD, {"zod": {"4.6.7"}})


def test_pip_audit_vulnerabilities_are_deduplicated_and_sorted() -> None:
    vuln = {"id": "PYSEC-1", "fix_versions": ["1.25.9", "1.24.2"], "aliases": ["GHSA-b", "CVE-1"]}
    payload = {"dependencies": [{"name": "urllib3", "version": "1.24.1", "vulns": [vuln, vuln]}]}

    found = audit.parse_pip_audit("uv.lock", json.dumps(payload))

    assert [item.render() for item in found] == [
        "SECURITY.dependency-audit lock=uv.lock ecosystem=PyPI package=urllib3 version=1.24.1 "
        "advisory=PYSEC-1 aliases=CVE-1,GHSA-b fix=1.24.2,1.25.9"
    ]


def test_classify_fails_unknown_locks_and_manifests_without_locks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "web").mkdir()
    (tmp_path / "web/package.json").write_text('{"dependencies": {"a": "1.0.0"}}', encoding="utf-8")
    (tmp_path / "web/yarn.lock").write_text("", encoding="utf-8")
    (tmp_path / "tool").mkdir()
    (tmp_path / "tool/package.json").write_text('{"name": "tool"}', encoding="utf-8")
    (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")

    scope = audit.classify(["web/package.json", "web/yarn.lock", "tool/package.json", "go.mod"])

    assert sorted((item.kind, item.path) for item in scope.failures) == [
        ("manifest-without-lock", "go.mod"),
        ("manifest-without-lock", "web/package.json"),
        ("unknown-lock-format", "web/yarn.lock"),
    ]
    assert scope.manifests == ["go.mod", "tool/package.json", "web/package.json"]


def test_routine_scope_drops_named_exclusions_and_fails_stale_ones(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    excluded = [
        {"path": "a/bun.lock", "origin": "fixture"},
        {"path": "gone/uv.lock", "origin": "x"},
    ]
    path.write_text(json.dumps({"excluded": excluded, "exceptions": []}), encoding="utf-8")
    policy = audit.load_policy(path)

    files = audit.routine_scope(["a/bun.lock", "b/bun.lock"], policy)

    assert files == ["b/bun.lock"]
    assert [item.reason for item in policy.failures] == [
        "excluded path is not listed: gone/uv.lock"
    ]


def test_no_fix_exception_suppresses_only_its_advisory(tmp_path: Path) -> None:
    policy = _policy(tmp_path, [_entry()])
    other = audit.Advisory("app/bun.lock", "npm", "zod", "3.25.76", "GHSA-y", (), ())

    left, failures = audit.apply_exceptions(
        [_advisory(()), other], policy, {"app/bun.lock"}, _TODAY
    )

    assert left == [other]
    assert failures == []


@pytest.mark.parametrize(
    ("entry", "fixes", "problem"),
    [
        (_entry(expires="2026-11-26"), (), "entry expired on 2026-11-26"),
        (_entry(expires="2026-02-30"), (), "expires is not a calendar date"),
        (_entry(advisory="GHSA-z"), (), "entry matches no reported advisory"),
        (_entry(), ("4.6.6",), "advisory has fix versions 4.6.6; upgrade instead"),
        ({"advisory": "GHSA-x"}, (), "entry fields must be exactly"),
    ],
    ids=["expired", "bad-date", "unmatched", "unblocked-fix", "malformed"],
)
def test_invalid_exceptions_fail_and_keep_the_advisory(
    tmp_path: Path, entry: dict[str, str], fixes: tuple[str, ...], problem: str
) -> None:
    advisory = _advisory(fixes)

    left, failures = audit.apply_exceptions(
        [advisory], _policy(tmp_path, [entry]), {"app/bun.lock"}, _TODAY
    )

    assert left == [advisory]
    assert len(failures) == 1
    assert problem in failures[0].reason


def test_blocked_fix_exception_needs_a_locked_blocker_that_excludes_every_fix(
    tmp_path: Path,
) -> None:
    lock = tmp_path / "bun.lock"
    lock.write_text(
        '{"lockfileVersion": 1, "packages": {"zod": ["zod@3.25.76"], "host": ["host@2.0.0"]}}',
        encoding="utf-8",
    )
    advisory = audit.Advisory(lock.as_posix(), "npm", "zod", "3.25.76", "GHSA-x", (), ("4.6.6",))
    blocked = _entry(
        lock=lock.as_posix(),
        blocked_by="host@2.0.0",
        constraint="host@2.0.0 requires zod@^3.25.0",
        constraint_source="https://registry.npmjs.org/host/2.0.0",
    )
    allows = {**blocked, "constraint": "host@2.0.0 requires zod@>=3.25.0"}

    kept, accepted = audit.apply_exceptions(
        [advisory], _policy(tmp_path, [blocked]), {lock.as_posix()}, _TODAY
    )
    left, failures = audit.apply_exceptions(
        [advisory], _policy(tmp_path, [allows]), {lock.as_posix()}, _TODAY
    )

    assert (kept, accepted) == ([], [])
    assert left == [advisory]
    assert "constraint allows fixed version 4.6.6" in failures[0].reason


def test_failure_line_keeps_reason_on_one_line() -> None:
    line = audit.Failure("auditor", "uv.lock", "uv export failed:\n  bad").render()

    assert (
        line
        == "SECURITY.dependency-audit failure=auditor path=uv.lock reason=uv export failed: bad"
    )
