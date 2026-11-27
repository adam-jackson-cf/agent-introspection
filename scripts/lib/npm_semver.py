from __future__ import annotations

import re
from dataclasses import dataclass

_VERSION = re.compile(
    r"^v?(?P<major>\d+)(?:\.(?P<minor>\d+|[xX*]))?(?:\.(?P<patch>\d+|[xX*]))?"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)
_COMPARATOR = re.compile(r"^(?P<op><=|>=|<|>|=|\^|~)?(?P<version>\S+)$")
_WILDCARDS = frozenset({"", "*", "x", "X"})


class RangeError(ValueError):
    __slots__ = ()


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int
    patch: int
    release: int
    pre: tuple[tuple[int, int | str], ...]

    def render(self) -> str:
        core = f"{self.major}.{self.minor}.{self.patch}"
        if not self.pre:
            return core
        return core + "-" + ".".join(str(part) for _, part in self.pre)


@dataclass(frozen=True)
class Partial:
    parts: tuple[int, ...]
    pre: tuple[tuple[int, int | str], ...]

    def floor(self) -> Version:
        padded = (*self.parts, 0, 0)[:3]
        return Version(padded[0], padded[1], padded[2], 0 if self.pre else 1, self.pre)

    def ceiling(self, depth: int) -> Version:
        bumped = [*self.parts[:depth], 0, 0, 0][:3]
        bumped[depth - 1] += 1
        return Version(bumped[0], bumped[1], bumped[2], 0, ((0, 0),))


Comparator = tuple[str, Version]


def _pre(text: str | None) -> tuple[tuple[int, int | str], ...]:
    if not text:
        return ()
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in text.split("."))


def _partial(text: str) -> Partial:
    if text in _WILDCARDS:
        return Partial((), ())
    match = _VERSION.match(text)
    if match is None:
        raise RangeError(f"unparsable version: {text}")
    parts: list[int] = []
    for name in ("major", "minor", "patch"):
        value = match.group(name)
        if value is None or value in _WILDCARDS:
            break
        parts.append(int(value))
    return Partial(tuple(parts), _pre(match.group("pre")))


def parse_version(text: str) -> Version:
    partial = _partial(text)
    if len(partial.parts) != 3:
        raise RangeError(f"version is not exact: {text}")
    return partial.floor()


def _caret(partial: Partial) -> list[Comparator]:
    parts = partial.parts
    if not parts:
        return []
    depth = next((index + 1 for index, part in enumerate(parts) if part), len(parts))
    return [(">=", partial.floor()), ("<", partial.ceiling(depth))]


def _tilde(partial: Partial) -> list[Comparator]:
    if not partial.parts:
        return []
    depth = min(len(partial.parts), 2)
    return [(">=", partial.floor()), ("<", partial.ceiling(depth))]


def _plain(op: str, partial: Partial) -> list[Comparator]:
    depth = len(partial.parts)
    if depth == 0:
        return [] if op in {"", "=", ">=", "<="} else [("<", Version(0, 0, 0, 0, ((0, 0),)))]
    if depth == 3:
        return [(op or "=", partial.floor())]
    bounds = {
        "": [(">=", partial.floor()), ("<", partial.ceiling(depth))],
        "=": [(">=", partial.floor()), ("<", partial.ceiling(depth))],
        ">=": [(">=", partial.floor())],
        ">": [(">=", partial.ceiling(depth))],
        "<": [("<", partial.floor())],
        "<=": [("<", partial.ceiling(depth))],
    }
    return bounds[op]


def _comparators(text: str) -> list[Comparator]:
    match = _COMPARATOR.match(text)
    if match is None:
        raise RangeError(f"unparsable comparator: {text}")
    op, partial = match.group("op") or "", _partial(match.group("version"))
    if op == "^":
        return _caret(partial)
    if op == "~":
        return _tilde(partial)
    return _plain(op, partial)


def _conjunction(text: str) -> list[Comparator]:
    tokens = re.sub(r"(<=|>=|<|>|=|\^|~)\s+", r"\1", text).split()
    if len(tokens) == 3 and tokens[1] == "-":
        low, high = _partial(tokens[0]), _partial(tokens[2])
        upper: list[Comparator] = (
            [("<=", high.floor())] if len(high.parts) == 3 else _plain("<=", high)
        )
        return [(">=", low.floor()), *upper]
    if "-" in tokens:
        raise RangeError(f"unparsable hyphen range: {text}")
    return [item for token in tokens for item in _comparators(token)]


def parse_range(text: str) -> list[list[Comparator]]:
    if not text.strip():
        raise RangeError("empty range")
    return [_conjunction(part) for part in text.split("||")]


def _holds(version: Version, comparator: Comparator) -> bool:
    op, bound = comparator
    checks = {
        "=": version == bound,
        "<": version < bound,
        "<=": version <= bound,
        ">": version > bound,
        ">=": version >= bound,
    }
    return checks[op]


def satisfies(version: Version, ranges: list[list[Comparator]]) -> bool:
    return any(all(_holds(version, item) for item in conjunction) for conjunction in ranges)


def fix_versions(version: Version, ranges: list[list[Comparator]]) -> list[str]:
    fixes: set[str] = set()
    for conjunction in ranges:
        if not all(_holds(version, item) for item in conjunction):
            continue
        uppers = [bound for op, bound in conjunction if op == "<" and bound.release]
        if not uppers:
            return []
        fixes.add(min(uppers).render())
    return sorted(fixes, key=parse_version)
