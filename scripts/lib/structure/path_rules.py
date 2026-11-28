from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterator
from fnmatch import fnmatchcase
from functools import lru_cache
from pathlib import PurePosixPath

from lib.structure.finding import Finding
from lib.structure.scope import Scope
from lib.structure.settings import Settings

NAME = "NAME.conventions"
PLACEMENT = "STRUCT.folder-placement"
PREFIX = "PREFIX.candidates"
_SEPARATORS = re.compile(r"[_.\-]+")
_CASE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_VERSION = re.compile(r"(?<![A-Za-z0-9])v\d+(?![A-Za-z0-9])")
_STANDALONE_VERSION = re.compile(r"^v\d+$")
_VERSION_TOKEN = re.compile(r"[-_.]?(?<![A-Za-z0-9])v\d+(?![A-Za-z0-9])")
_JOINERS = {"kebab-case": "-", "snake_case": "_", "lowercase": "_"}


def name_tokens(name: str) -> list[str]:
    return [
        token.lower()
        for part in _SEPARATORS.split(name)
        for token in _CASE_BOUNDARY.split(part)
        if token
    ]


def name_stem(name: str) -> str:
    if name.startswith("."):
        return "." + name[1:].split(".", 1)[0]
    return name.split(".", 1)[0]


@lru_cache(maxsize=256)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    translated = ""
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            translated, index = translated + "(?:.*/)?", index + 3
        elif pattern.startswith("**", index):
            translated, index = translated + ".*", index + 2
        else:
            char = pattern[index]
            translated += {"*": "[^/]*", "?": "[^/]"}.get(char, re.escape(char))
            index += 1
    return re.compile(f"^{translated}$")


def glob_match(pattern: str, path: str) -> bool:
    return bool(_glob_regex(pattern).match(path))


def repeats_parent(parent: str, child: str) -> bool:
    parent_tokens, child_tokens = name_tokens(parent), name_tokens(child)
    width = len(parent_tokens)
    if not width:
        return False
    return any(
        child_tokens[start : start + width] == parent_tokens
        for start in range(len(child_tokens) - width + 1)
    )


def unversioned(name: str) -> str | None:
    if _STANDALONE_VERSION.match(name) or not _VERSION.search(name):
        return None
    return _VERSION_TOKEN.sub("", name).strip("-_.") or "<behaviour-name>"


def _is_plural(token: str) -> bool:
    return token.endswith("s") and not token.endswith(("ss", "us", "is"))


def _finding(path: str, rule: str, check: str, problem: str, fix: str) -> Finding:
    return Finding(path=path, line=0, rule=rule, check=check, problem=problem, fix=fix)


def _version_finding(segment_path: str, name: str) -> Iterator[Finding]:
    corrected = unversioned(name)
    if corrected is not None:
        yield _finding(
            segment_path,
            NAME,
            "version",
            f"'{name}' carries a version token",
            f"rename it to '{corrected}'; versions live only in <name>/v<N> folders or files",
        )


def _repeat_finding(segment_path: str, parent: str, name: str) -> Iterator[Finding]:
    if parent and repeats_parent(parent, name):
        yield _finding(
            segment_path,
            NAME,
            "child-repeats-parent",
            f"'{name}' repeats its parent '{parent}'",
            f"drop the '{parent}' tokens from the name",
        )


def _directory_findings(
    settings: Settings, segment_path: str, parent: str, python_owned: bool
) -> Iterator[Finding]:
    name = segment_path.rsplit("/", 1)[-1]
    if name in settings.banned_words and not python_owned:
        yield _finding(
            segment_path,
            PLACEMENT,
            "grab-bag",
            f"directory '{name}' is a grab-bag folder",
            "move each file under its one owner, or shared code into the lowest tier that "
            "reads it, in a folder named for its content",
        )
    tokens = name_tokens(name)
    if tokens and _is_plural(tokens[-1]) and tokens[-1] not in settings.catalogue_plurals:
        yield _finding(
            segment_path,
            NAME,
            "plural",
            f"directory '{name}' is plural",
            f"rename it to the singular '{name[:-1]}', or have the owner add '{tokens[-1]}' "
            "to [structure].catalogue_plurals when it holds a catalogue of named items",
        )
    yield from _version_finding(segment_path, name)
    yield from _repeat_finding(segment_path, parent, name)


def _casing_finding(settings: Settings, path: str, stem: str) -> Iterator[Finding]:
    for pattern, casing in sorted(settings.casing.items()):
        if glob_match(pattern, path) and not re.fullmatch(settings.casing_patterns[casing], stem):
            joiner = _JOINERS.get(casing, "-")
            yield _finding(
                path,
                NAME,
                "casing",
                f"'{stem}' is not {casing}",
                f"rename it to '{joiner.join(name_tokens(stem))}'",
            )


def _file_findings(settings: Settings, path: str, parent: str) -> Iterator[Finding]:
    name = path.rsplit("/", 1)[-1]
    if settings.is_fixed(name):
        return
    stem = name_stem(name)
    python_owned = PurePosixPath(name).suffix in settings.pyq_owned_extensions
    if stem in settings.banned_words and not python_owned:
        yield _finding(
            path,
            NAME,
            "banned-word",
            f"'{stem}' is a banned word",
            "rename it with a plain domain word for what it holds or does",
        )
    yield from _version_finding(path, stem)
    if not python_owned:
        yield from _casing_finding(settings, path, stem)
    if name not in settings.repeat_exempt_files:
        yield from _repeat_finding(path, parent, stem)


def _new_segments(scope: Scope, path: str) -> Iterator[tuple[int, str, str]]:
    parts = path.split("/")
    for index in range(len(parts)):
        segment_path = "/".join(parts[: index + 1])
        if scope.is_new(segment_path):
            yield index, segment_path, parts[index - 1] if index else ""


def naming_findings(settings: Settings, scope: Scope) -> set[Finding]:
    findings: set[Finding] = set()
    for path in scope.judged:
        last = path.count("/")
        python_owned = PurePosixPath(path).suffix in settings.pyq_owned_extensions
        for index, segment_path, parent in _new_segments(scope, path):
            if index == last:
                findings.update(_file_findings(settings, path, parent))
            else:
                findings.update(_directory_findings(settings, segment_path, parent, python_owned))
    return findings


def _owned_by_map(settings: Settings, path: str) -> bool:
    if "/" not in path and settings.is_fixed(path):
        return True
    return any(
        path.startswith(key) if key.endswith("/") else path == key for key in settings.owners
    )


def _owner_finding(settings: Settings, path: str) -> Iterator[Finding]:
    if not _owned_by_map(settings, path):
        folders = ", ".join(sorted(key for key in settings.owners if key.endswith("/")))
        yield _finding(
            path,
            PLACEMENT,
            "owner",
            "no [structure].owners entry owns this path",
            f"move it under its owner folder ({folders}) or have the owner map it in "
            "[structure].owners",
        )


def _skill_findings(settings: Settings, path: str, head: frozenset[str]) -> Iterator[Finding]:
    layout = settings.skill_layout
    if not path.startswith(f"{layout.root}/"):
        return
    skill, _, inner = path[len(layout.root) + 1 :].partition("/")
    allowed = " | ".join(layout.files)
    if not inner or not any(glob_match(pattern, inner) for pattern in layout.files):
        yield _finding(
            path,
            PLACEMENT,
            "skill-layout",
            f"'{inner or skill}' is outside the skill layout",
            f"move it to {layout.root}/<kebab-name>/ as one of: {allowed}",
        )
    if inner and f"{layout.root}/{skill}/{layout.marker}" not in head:
        yield _finding(
            path,
            PLACEMENT,
            "instance-marker",
            f"skill folder '{skill}' has no {layout.marker}",
            f"add {layout.root}/{skill}/{layout.marker} with the skill's metadata",
        )


def placement_findings(settings: Settings, scope: Scope) -> set[Finding]:
    head = frozenset(scope.head_paths)
    findings: set[Finding] = set()
    for path in scope.judged:
        findings.update(_owner_finding(settings, path))
        findings.update(_skill_findings(settings, path, head))
    return findings


def _test_core(settings: Settings, name: str) -> str:
    for pattern in settings.test_name_patterns:
        if pattern.count("*") == 1 and fnmatchcase(name, pattern):
            prefix, suffix = pattern.split("*")
            return name[len(prefix) : len(name) - len(suffix)]
    return name


def cluster_key(settings: Settings, name: str) -> str | None:
    tokens = name_tokens(name_stem(_test_core(settings, name)))
    if len(tokens) < 2 or tokens[0].isdigit():
        return None
    return tokens[0]


def _children(paths: tuple[str, ...]) -> dict[str, set[str]]:
    children: dict[str, set[str]] = defaultdict(set)
    for path in paths:
        parts = path.split("/")
        for index, name in enumerate(parts):
            children["/".join(parts[:index])].add(name)
    return children


def _cluster_glob(parent: str, name: str, key: str) -> str:
    end = name.lower().find(key) + len(key)
    if end < len(name) and name[end] in "_-.":
        end += 1
    return f"{parent}/{name[:end]}*" if parent else f"{name[:end]}*"


def _prefix_finding(
    settings: Settings, siblings: set[str], parent: str, name: str
) -> Iterator[Finding]:
    key = cluster_key(settings, name)
    if key is None:
        return
    members = sorted(sibling for sibling in siblings if cluster_key(settings, sibling) == key)
    stems = {name_stem(_test_core(settings, member)) for member in members}
    if len(stems) < 2:
        return
    segment_path = f"{parent}/{name}" if parent else name
    glob = _cluster_glob(parent, name, key)
    yield _finding(
        segment_path,
        PREFIX,
        "cluster",
        f"'{name}' joins the sibling prefix cluster '{glob}' ({', '.join(members)})",
        f"extract the '{key}' concept into its own folder, or have the owner record "
        f'[[exception]] rule = "{PREFIX}" path = "{glob}"',
    )


def prefix_findings(settings: Settings, scope: Scope) -> set[Finding]:
    children = _children(scope.head_paths + scope.judged)
    findings: set[Finding] = set()
    for path in scope.judged:
        for _, segment_path, _ in _new_segments(scope, path):
            parent, _, name = segment_path.rpartition("/")
            findings.update(_prefix_finding(settings, children[parent], parent, name))
    return findings
