from __future__ import annotations

import re
from dataclasses import dataclass

_ENV_ASSIGNMENT = r"^[A-Za-z_][A-Za-z0-9_]*="
_SUBCOMMAND = r"^[a-z][a-z0-9:_.-]*$"
_SEPARATOR = r"\s*(?:&&|\|\||;|\||\n)\s*"
_WHITESPACE = r"\s+"
_SHELL_C = r"(?s)^\s*(?:\S*/)?(?:bash|sh|zsh)\s+-[a-z]*c\s+(.*?)\s*$"
_QUOTES = r"^[\"']|[\"']$"
_PYTHON = r"^python[0-9.]*$"
_DURATION = r"^[0-9.]+[smhd]?$"

MAX_UNWRAPS = 3
SKIPPED_HEADS = ("cd", "pushd", "popd", "export", "set", "source")
SHELLS = ("bash", "sh", "zsh")
SCRIPT_RUNNERS = ("npm", "pnpm", "yarn", "bun")
SIMPLE_WRAPPERS = ("uvx", "npx", "bunx", "time", "nohup", "sudo", "caffeinate")
PAIR_WRAPPERS = (
    "uv run",
    "pnpm exec",
    "pnpm dlx",
    "npm exec",
    "yarn dlx",
    "poetry run",
    "pipx run",
)

SUBCOMMAND_HEADS = frozenset((
    "aws",
    "brew",
    "bun",
    "bunx",
    "cargo",
    "claude",
    "clawpatch",
    "codegraph",
    "codex",
    "defaults",
    "deno",
    "docker",
    "gcloud",
    "gh",
    "git",
    "go",
    "hdiutil",
    "helm",
    "infisical",
    "just",
    "kubectl",
    "launchctl",
    "make",
    "npm",
    "npx",
    "omp",
    "orca",
    "pip",
    "pipx",
    "pnpm",
    "poetry",
    "security",
    "systemctl",
    "terraform",
    "uv",
    "volta",
    "yarn",
))

_RUNNERS = r"^(?:npm|pnpm|yarn|bun|make|just)(?: run)? "


def _alias(names: str) -> str:
    return rf"{_RUNNERS}(?:{names})(?: |$)"


_PYTEST_INTEGRATION = r"^(?:pytest|py\.test)(?: |.* )(?:-m integration|\S*tests?/integration)"
_TEST_PROGRAMS = r"pytest|py\.test|unittest|vitest|jest|mocha|ava|tap|rspec|phpunit"
_SMOKE_FLAG = rf"^(?:{_TEST_PROGRAMS})(?: |.* )--smoke(?: |$)"

type Rule = tuple[str, tuple[str, ...], str]
KIND_RULES: tuple[Rule, ...] = (
    (
        "unit_test",
        (
            rf"^(?:{_TEST_PROGRAMS})(?: |$)",
            r"^(?:bun|cargo|go|dotnet|swift|mix) test(?: |$)",
            _alias(r"test|tests|unit|test:unit|test:\S*unit\S*"),
        ),
        rf"{_PYTEST_INTEGRATION}|{_SMOKE_FLAG}",
    ),
    (
        "integration_e2e",
        (
            r"^(?:playwright|cypress|detox|k6|newman)(?: |$)",
            _PYTEST_INTEGRATION,
            _alias(r"e2e|integration|test:e2e|test:integration|itest"),
        ),
        "",
    ),
    ("smoke", (_SMOKE_FLAG, _alias(r"smoke|test:smoke")), ""),
    (
        "lint_format",
        (
            r"^(?:ruff|eslint|oxlint|prettier|black|isort|flake8|pylint|shellcheck|stylelint"
            r"|gofmt|dprint)(?: |$)",
            r"^biome (?:check|lint|format)(?: |$)",
            r"^cargo (?:clippy|fmt)(?: |$)",
            r"^go vet(?: |$)",
            _alias(r"lint|format|fmt|lint:\S+|format:\S+|style"),
        ),
        "",
    ),
    (
        "typecheck",
        (
            r"^(?:tsc|mypy|pyright|basedpyright|flow)(?: |$)",
            r"^cargo check(?: |$)",
            _alias(r"typecheck|types|check-types|tsc"),
        ),
        "",
    ),
    (
        "ci_remote",
        (
            r"^(?:act|circleci|gitlab-runner)(?: |$)",
            r"^gh (?:run|workflow) (?:view|watch|list)(?: |$)",
            r"^gh pr checks(?: |$)",
        ),
        "",
    ),
)
_GATE_NAMES = r"(?:run-)?(?:ci|gates?|quality-gates?|check-all)(?:\.\w+)?"
GATE_PATTERNS = (
    rf"^{_GATE_NAMES}(?: |$)",
    r"^(?:pre-commit|lefthook|husky)(?: |$)",
    rf"^python[0-9.]* (?:\S*/)?{_GATE_NAMES}(?: |$)",
    r"^(?:(?:npm|bun|pnpm) run|yarn(?: run)?) "
    r"(?:check|ci|verify|validate|qa|all|gate|gates|precommit|pre-commit)(?: |$)",
    r"^(?:make|just) (?:check|ci|all|verify)(?: |$)",
)

_COMPILED_RULES = tuple(
    (
        kind,
        tuple(re.compile(p, re.ASCII) for p in include),
        re.compile(exclude, re.ASCII) if exclude else None,
    )
    for kind, include, exclude in KIND_RULES
)
_COMPILED_GATES = tuple(re.compile(p, re.ASCII) for p in GATE_PATTERNS)


@dataclass(frozen=True)
class CommandShape:
    head: str
    sub: str
    segments: int
    verification_kinds: tuple[str, ...]
    gate_run: int


def _re(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.ASCII)


def _basename(token: str) -> str:
    return token.split("/")[-1]


def _at(tokens: list[str], index: int) -> str:
    return tokens[index] if index < len(tokens) else ""


def command_tokens(command: str) -> list[str]:
    env = _re(_ENV_ASSIGNMENT)
    return [token for token in _re(_WHITESPACE).split(command) if token and not env.match(token)]


def _strip_flags(tokens: list[str]) -> list[str]:
    for index, token in enumerate(tokens):
        if not token.startswith("-"):
            return tokens[index:]
    return []


def _script_skip(tokens: list[str]) -> int:
    for index, token in enumerate(tokens[1:], start=1):
        if not token.startswith("-"):
            return index if token.endswith(".sh") else 0
    return 0


def _wrapper_skip(tokens: list[str]) -> int:
    head, second = _basename(tokens[0]), _at(tokens, 1)
    if head in SIMPLE_WRAPPERS:
        return 1
    if f"{head} {second}" in PAIR_WRAPPERS:
        return 2
    if head == "timeout":
        return 2 if _re(_DURATION).match(second) else 1
    if _re(_PYTHON).match(head) and second == "-m" and len(tokens) > 2:
        return 2
    return _script_skip(tokens) if head in SHELLS else 0


def _unwrap(tokens: list[str]) -> list[str]:
    for _ in range(MAX_UNWRAPS):
        skip = _wrapper_skip(tokens)
        rest = _strip_flags(tokens[skip:]) if skip else []
        if not rest:
            break
        tokens = rest
    return tokens


def _segments(command: str) -> list[list[str]]:
    shell = _re(_SHELL_C).match(command)
    text = _re(_QUOTES).sub("", shell[1]) if shell else command
    env, whitespace = _re(_ENV_ASSIGNMENT), _re(_WHITESPACE)
    found: list[list[str]] = []
    for piece in _re(_SEPARATOR).split(text):
        tokens = [token for token in whitespace.split(piece) if token]
        lead = next(
            (i for i, t in enumerate(tokens) if not (env.match(t) or t in ("env", "exec"))),
            len(tokens),
        )
        if tokens[lead:]:
            found.append(_unwrap(tokens[lead:]))
    return found


def _shape_text(tokens: list[str]) -> str:
    return " ".join([_basename(tokens[0]), *tokens[1:]])


def _sub(head: str, tokens: list[str]) -> str:
    second, subcommand = _at(tokens, 1), _re(_SUBCOMMAND)
    if head in SCRIPT_RUNNERS and second == "run" and len(tokens) > 2:
        return tokens[2] if subcommand.match(tokens[2]) else ""
    if _re(_PYTHON).match(head) and second.endswith(".py"):
        return _basename(second) if subcommand.match(_basename(second)) else ""
    if head in SUBCOMMAND_HEADS and subcommand.match(second):
        return second
    return ""


def _matches(
    include: tuple[re.Pattern[str], ...], exclude: re.Pattern[str] | None, shapes: list[str]
) -> bool:
    return any(
        any(p.search(shape) for p in include) and not (exclude and exclude.search(shape))
        for shape in shapes
    )


def command_shape(command: str) -> CommandShape:
    segments = _segments(command)
    shapes = [_shape_text(tokens) for tokens in segments]
    main = next((t for t in segments if _basename(t[0]) not in SKIPPED_HEADS), None)
    main = main if main is not None else (segments[0] if segments else [])
    head = _basename(main[0]) if main else ""
    kinds = sorted(
        kind for kind, include, exclude in _COMPILED_RULES if _matches(include, exclude, shapes)
    )
    gate = _matches(_COMPILED_GATES, None, shapes)
    return CommandShape(
        head, _sub(head, main) if main else "", len(segments), tuple(kinds), int(gate)
    )


def _literal(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _list(items: tuple[str, ...]) -> str:
    return ", ".join(_literal(item) for item in items)


def _kind_array() -> str:
    clauses = []
    for kind, include, exclude in KIND_RULES:
        matched = " OR ".join(f"match(j, {_literal(p)})" for p in include)
        if exclude:
            matched = f"({matched}) AND NOT match(j, {_literal(exclude)})"
        clauses.append(f"if(arrayExists(j -> {matched}, cs_shapes), {_literal(kind)}, '')")
    return "[" + ", ".join(clauses) + "]"


def _wrapper_step() -> str:
    script = "arrayFirstIndex(t -> NOT startsWith(t, '-'), arraySlice(acc, 2))"
    skip = (
        "arrayMap(h -> multiIf("
        f"h IN ({_list(SIMPLE_WRAPPERS)}), 1, "
        f"concat(h, ' ', acc[2]) IN ({_list(PAIR_WRAPPERS)}), 2, "
        f"h = 'timeout', if(match(acc[2], {_literal(_DURATION)}), 2, 1), "
        f"match(h, {_literal(_PYTHON)}) AND acc[2] = '-m' AND length(acc) > 2, 2, "
        f"h IN ({_list(SHELLS)}), "
        f"arrayMap(m -> if(m > 0 AND endsWith(acc[m + 1], '.sh'), m, 0), [{script}])[1], "
        "0), [arrayElement(splitByChar('/', acc[1]), -1)])[1]"
    )
    return (
        "arrayMap(n -> if(n = 0, acc, "
        "arrayMap(r -> arrayMap(k -> if(k = 0, acc, arraySlice(r, k)), "
        "[arrayFirstIndex(t -> NOT startsWith(t, '-'), r)])[1], [arraySlice(acc, n + 1)])[1]), "
        f"[{skip}])[1]"
    )


def fill(template: str, values: dict[str, str]) -> str:
    for name, value in values.items():
        template = template.replace("{" + name + "}", value)
    return template


def sql_parts() -> dict[str, str]:
    return {
        "shell_c": _literal(_SHELL_C),
        "quotes": _literal(_QUOTES),
        "separator": _literal(_SEPARATOR),
        "whitespace": _literal(_WHITESPACE),
        "env_assignment": _literal(_ENV_ASSIGNMENT),
        "subcommand": _literal(_SUBCOMMAND),
        "python": _literal(_PYTHON),
        "max_unwraps": str(MAX_UNWRAPS),
        "wrapper_step": _wrapper_step(),
        "skipped_heads": _list(SKIPPED_HEADS),
        "script_runners": _list(SCRIPT_RUNNERS),
        "subcommand_heads": _list(tuple(sorted(SUBCOMMAND_HEADS))),
        "kinds": _kind_array(),
        "gate": " OR ".join(f"match(j, {_literal(p)})" for p in GATE_PATTERNS),
    }
