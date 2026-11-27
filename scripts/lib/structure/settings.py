from __future__ import annotations

import tomllib
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from lib import filesystem

STRUCTURE_RULES = frozenset({
    "NAME.conventions",
    "PREFIX.candidates",
    "RENAME.residue",
    "STRUCT.dependency-direction",
    "STRUCT.folder-placement",
    "STRUCT.sibling-isolation",
    "STRUCT.single-owner-placement",
    "STRUCT.tier-readership",
})
_STRUCTURE_KEYS = frozenset({
    "banned_words",
    "casing",
    "casing_patterns",
    "catalogue_plurals",
    "fixed_files",
    "import_package",
    "owners",
    "ownership_areas",
    "pyq_owned_extensions",
    "repeat_exempt_files",
    "skill_layout",
    "source_root",
    "test_name_patterns",
    "tier_modules",
    "tiers",
})
_SKILL_LAYOUT_KEYS = frozenset({"files", "marker", "root"})
_EXCEPTION_KEYS = frozenset({"approved_by", "check", "path", "reason", "rule"})


class SettingsError(Exception):
    pass


@dataclass(frozen=True)
class StructureException:
    rule: str
    path: str
    check: str | None

    def covers(self, rule: str, path: str, check: str) -> bool:
        if rule != self.rule or self.check not in {None, check}:
            return False
        if any(char in self.path for char in "*?["):
            return fnmatchcase(path, self.path)
        plain = self.path.rstrip("/")
        return path == plain or path.startswith(f"{plain}/")


@dataclass(frozen=True)
class SkillLayout:
    root: str
    marker: str
    files: tuple[str, ...]


@dataclass(frozen=True)
class Settings:
    base_ref: str
    package_dir: str
    import_package: str
    tiers: tuple[str, ...]
    tier_of: dict[str, str]
    owners: dict[str, str]
    banned_words: frozenset[str]
    catalogue_plurals: frozenset[str]
    fixed_files: tuple[str, ...]
    repeat_exempt_files: frozenset[str]
    pyq_owned_extensions: frozenset[str]
    test_name_patterns: tuple[str, ...]
    casing: dict[str, str]
    casing_patterns: dict[str, str]
    skill_layout: SkillLayout
    fixtures_dir: str
    exceptions: tuple[StructureException, ...]

    def excepted(self, rule: str, path: str, check: str) -> bool:
        return any(entry.covers(rule, path, check) for entry in self.exceptions)

    def is_fixed(self, name: str) -> bool:
        return any(fnmatchcase(name, pattern) for pattern in self.fixed_files)

    def is_fixture(self, path: str) -> bool:
        return path == self.fixtures_dir or path.startswith(f"{self.fixtures_dir}/")


def _table(parent: dict[str, object], key: str, where: str) -> dict[str, object]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise SettingsError(f"{where}.{key} must be a table")
    return value


def _string(table: dict[str, object], key: str, where: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value:
        raise SettingsError(f"{where}.{key} must be a nonempty string")
    return value


def _strings(table: dict[str, object], key: str, where: str) -> tuple[str, ...]:
    value = table.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise SettingsError(f"{where}.{key} must be a list of nonempty strings")
    return tuple(value)


def _string_map(table: dict[str, object], key: str, where: str) -> dict[str, str]:
    mapping = _table(table, key, where)
    if not all(isinstance(value, str) and value for value in mapping.values()):
        raise SettingsError(f"{where}.{key} values must be nonempty strings")
    return {name: str(value) for name, value in mapping.items()}


def _reject_unknown(table: dict[str, object], allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise SettingsError(f"{where} has unknown keys: {', '.join(unknown)}")


def _tier_of(structure: dict[str, object], tiers: tuple[str, ...]) -> dict[str, str]:
    modules = _table(structure, "tier_modules", "[structure]")
    if set(modules) != set(tiers):
        raise SettingsError("[structure].tier_modules must have exactly one list per tier")
    tier_of: dict[str, str] = {}
    for tier in tiers:
        for part in _strings(modules, tier, "[structure].tier_modules"):
            if part in tier_of:
                raise SettingsError(f"[structure].tier_modules lists '{part}' in two tiers")
            tier_of[part] = tier
    return tier_of


def _skill_layout(structure: dict[str, object]) -> SkillLayout:
    layout = _table(structure, "skill_layout", "[structure]")
    _reject_unknown(layout, _SKILL_LAYOUT_KEYS, "[structure].skill_layout")
    where = "[structure].skill_layout"
    return SkillLayout(
        root=_string(layout, "root", where).rstrip("/"),
        marker=_string(layout, "marker", where),
        files=_strings(layout, "files", where),
    )


def _exception(entry: object, index: int) -> StructureException | None:
    if not isinstance(entry, dict):
        raise SettingsError(f"exception[{index}] must be a table")
    if entry.get("rule") not in STRUCTURE_RULES:
        return None
    where = f"exception[{index}]"
    _reject_unknown(entry, _EXCEPTION_KEYS, where)
    _string(entry, "reason", where)
    _string(entry, "approved_by", where)
    check = entry.get("check")
    if check is not None and not (isinstance(check, str) and check):
        raise SettingsError(f"{where}.check must be a nonempty string")
    return StructureException(
        rule=_string(entry, "rule", where), path=_string(entry, "path", where), check=check
    )


def _exceptions(document: dict[str, object]) -> tuple[StructureException, ...]:
    entries = document.get("exception", [])
    if not isinstance(entries, list):
        raise SettingsError("[[exception]] must be an array of tables")
    parsed = (_exception(entry, index) for index, entry in enumerate(entries))
    return tuple(entry for entry in parsed if entry is not None)


def _settings(document: dict[str, object]) -> Settings:
    structure = _table(document, "structure", "proofs.toml")
    _reject_unknown(structure, _STRUCTURE_KEYS, "[structure]")
    where = "[structure]"
    tiers = _strings(structure, "tiers", where)
    package = _string(structure, "import_package", where)
    return Settings(
        base_ref=_string(_table(document, "scope", "proofs.toml"), "base_ref", "[scope]"),
        package_dir=f"{_string(structure, 'source_root', where).rstrip('/')}/{package}",
        import_package=package,
        tiers=tiers,
        tier_of=_tier_of(structure, tiers),
        owners=_string_map(structure, "owners", where),
        banned_words=frozenset(_strings(structure, "banned_words", where)),
        catalogue_plurals=frozenset(_strings(structure, "catalogue_plurals", where)),
        fixed_files=_strings(structure, "fixed_files", where),
        repeat_exempt_files=frozenset(_strings(structure, "repeat_exempt_files", where)),
        pyq_owned_extensions=frozenset(_strings(structure, "pyq_owned_extensions", where)),
        test_name_patterns=_strings(structure, "test_name_patterns", where),
        casing=_string_map(structure, "casing", where),
        casing_patterns=_string_map(structure, "casing_patterns", where),
        skill_layout=_skill_layout(structure),
        fixtures_dir=_string(_table(document, "layout", "proofs.toml"), "fixtures_dir", "[layout]"),
        exceptions=_exceptions(document),
    )


def load_settings(root: Path) -> Settings:
    path = root / "proofs.toml"
    try:
        document = tomllib.loads(filesystem.read_text(path))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise SettingsError(f"cannot read proofs.toml: {error}") from error
    settings = _settings(document)
    unknown_casing = sorted(set(settings.casing.values()) - set(settings.casing_patterns))
    if unknown_casing:
        raise SettingsError(
            f"[structure].casing names unknown casings: {', '.join(unknown_casing)}"
        )
    return settings
