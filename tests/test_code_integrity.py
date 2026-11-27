from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath
from types import ModuleType

import pytest

from tests.support.script_loader import load_script

_LAYOUT = (
    '[layout]\nsupport_dir = "tests/support"\nfixtures_dir = "tests/fixtures"\n'
    'test_file_patterns = ["tests/test_*.py", "web/**/*.test.ts"]\n'
)


@pytest.fixture(scope="module")
def checker() -> ModuleType:
    return load_script("check-code-integrity")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "proofs.toml").write_text(_LAYOUT, encoding="utf-8")
    return tmp_path


def _findings(
    checker: ModuleType, repo: Path, name: str, text: str, scope: set[int] | None = None
) -> list[str]:
    layouts = checker.Layouts(repo)
    found = checker.check_text(PurePosixPath(name), text, scope, layouts)
    return [finding.render() for finding in found]


def _comment_lines(checker: ModuleType, repo: Path, name: str, text: str) -> list[int]:
    return [
        int(line.split(":")[1])
        for line in _findings(checker, repo, name, text)
        if "CODE.no-comments" in line
    ]


def test_python_comments_and_docstrings_are_findings(checker: ModuleType, repo: Path) -> None:
    text = (
        '"""Module."""\n\n\nclass Box:\n    """Box."""\n\n    def size(self) -> int:\n'
        '        """Size."""\n        return 1  # one\n'
    )
    assert _comment_lines(checker, repo, "src/box.py", text) == [1, 5, 8, 9]


def test_python_tool_directives_shebang_and_coding_cookie_are_exempt(
    checker: ModuleType, repo: Path
) -> None:
    text = (
        "#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\nimport os  # noqa: F401\n"
        'x: int = "a"  # type: ignore[assignment]\ny = [1]  # fmt: skip\nz = "# text"\n'
    )
    assert _findings(checker, repo, "src/tool.py", text) == []


def test_script_lexer_skips_strings_templates_and_regex_literals(
    checker: ModuleType, repo: Path
) -> None:
    text = (
        'const a = "//no";\nconst b = /\\/\\*x/g;\n'
        "const c = `x ${a /* yes */} //no`;\nconst d = 'it\\'s'; // yes\n"
    )
    assert _comment_lines(checker, repo, "web/app.ts", text) == [3, 4]


def test_script_directives_are_exempt(checker: ModuleType, repo: Path) -> None:
    text = (
        "// @ts-expect-error reason\n// oxlint-disable-next-line no-console\n"
        '/* eslint-disable */\n// prettier-ignore\n/// <reference types="bun" />\nlet x = 1;\n'
    )
    assert _findings(checker, repo, "web/app.ts", text) == []


def test_shell_lexer_skips_quotes_parameter_lengths_and_heredocs(
    checker: ModuleType, repo: Path
) -> None:
    text = (
        "#!/bin/sh\n# shellcheck disable=SC2034\nn=${#x} m=$# s='a # b' t=\"c # d\" u=e#f\n"
        "cat <<'EOF'\n# data\nEOF\nread -r v <<<\"$n\"  # here-string\n# real\n"
    )
    assert _comment_lines(checker, repo, "run.sh", text) == [7, 8]


def test_yaml_lexer_skips_block_scalars_and_quoted_values(checker: ModuleType, repo: Path) -> None:
    text = (
        "a: \"x # y\"\nb: 'it''s # z'\nc: don't # real\nrun: |\n  # data\n  echo\n"
        "- run: >-\n    # data\nd: http://h/#x\n# real\n"
    )
    assert _comment_lines(checker, repo, "ci.yaml", text) == [3, 10]


def test_toml_sql_css_jsonc_and_line_comment_formats(checker: ModuleType, repo: Path) -> None:
    cases = {
        "a.toml": ('k = "#"\nm = """\n# data\n"""\n# real\n', [5]),
        "a.sql": ("SELECT '--', \"x--\" -- real\n/* real */\n", [1, 2]),
        "a.css": ('a { content: "/* x */"; } /* real */\n', [1]),
        "a.jsonc": ('{"u": "http://x", // real\n}\n', [1]),
        "a.service": ("[Unit]\n; real\nDescription=a # b\n", [2]),
        ".gitignore": ("# real\nbuild/\n", [1]),
        ".shellcheckrc": ("enable=all\n# real\n", [2]),
        ".editorconfig": ("root = true\n; real\n# real\n", [2, 3]),
    }
    for name, (text, lines) in cases.items():
        assert _comment_lines(checker, repo, name, text) == lines, name


def test_markdown_skips_code_and_exempts_tool_directives(checker: ModuleType, repo: Path) -> None:
    text = (
        "<!-- markdownlint-disable MD013 -->\n<!-- BEGIN GENERATED FROM x.toml -->\n"
        "Use `<!-- a -->`.\n\n```html\n<!-- data -->\n```\n<!-- real -->\n"
    )
    assert _comment_lines(checker, repo, "doc.md", text) == [8]


def test_file_type_without_a_lexer_fails_closed(checker: ModuleType, repo: Path) -> None:
    assert _findings(checker, repo, "notes.unknown", "text\n") == [
        "notes.unknown: CODE.no-comments: no comment syntax is known for this file type; "
        f"add its lexer to {sys.argv[0]}"
    ]


def test_scope_reports_only_comments_on_added_lines(checker: ModuleType, repo: Path) -> None:
    text = 'def f() -> int:\n    """Doc\n    more."""\n    return 1  # old\n# new\n'
    assert _comment_lines(checker, repo, "src/a.py", text) == [2, 4, 5]
    assert [
        line.split(":")[1] for line in _findings(checker, repo, "src/a.py", text, scope={3, 5})
    ] == ["2", "5"]


@pytest.mark.parametrize(
    "body",
    ["pass", "...", "raise NotImplementedError", 'raise NotImplementedError("later")'],
)
def test_placeholder_bodies_are_findings(checker: ModuleType, repo: Path, body: str) -> None:
    text = f"def todo() -> None:\n    {body}\n"
    assert _findings(checker, repo, "src/a.py", text) == [
        f"src/a.py:1: CODE.no-placeholder-bodies: function 'todo' has only a placeholder body "
        f"({body.split('(')[0]}); implement it, or declare it in a Protocol, with "
        "@abstractmethod or with @overload"
    ]


def test_declarations_and_stub_files_may_have_placeholder_bodies(
    checker: ModuleType, repo: Path
) -> None:
    text = (
        "import abc\nimport typing\n\n\nclass P(typing.Protocol[int]):\n"
        "    def a(self) -> int: ...\n\n\n"
        "class B(abc.ABC):\n    @abc.abstractmethod\n    def b(self) -> None:\n"
        "        raise NotImplementedError\n\n\n@typing.overload\ndef c(x: int) -> int: ...\n"
    )
    assert _findings(checker, repo, "src/a.py", text) == []
    assert _findings(checker, repo, "src/a.pyi", "def d() -> None: ...\n") == []


def test_python_mock_imports_outside_test_paths_are_findings(
    checker: ModuleType, repo: Path
) -> None:
    text = "import mock\nfrom unittest import mock as m\nimport pytest_mock.plugin\n"
    found = _findings(checker, repo, "src/a.py", text)
    assert [line.split(": ", 2)[0] for line in found] == ["src/a.py:1", "src/a.py:2", "src/a.py:3"]
    assert all("CODE.no-product-mocks" in line for line in found)
    for test_path in ("tests/test_a.py", "tests/support/a.py", "tests/fixtures/a.py"):
        assert _findings(checker, repo, test_path, text) == []


def test_script_mock_imports_and_calls_outside_test_paths_are_findings(
    checker: ModuleType, repo: Path
) -> None:
    text = (
        'import sinon from "sinon";\nconst p = require("proxyquire");\n'
        'vi.mock("./a");\njest . doMock("./b");\nconst s = "vi.mock(";\n'
    )
    found = _findings(checker, repo, "web/a.ts", text)
    assert [line.split(": ", 3)[2].split(";")[0] for line in found] == [
        "product code imports mock library 'sinon'",
        "product code imports mock library 'proxyquire'",
        "product code calls vi.mock()",
        "product code calls jest.doMock()",
    ]
    assert _findings(checker, repo, "web/deep/a.test.ts", text) == []


def test_missing_layout_names_the_proofs_file(checker: ModuleType, tmp_path: Path) -> None:
    found = _findings(checker, tmp_path, "src/a.py", "x = 1\n")
    assert found == [
        "proofs.toml: CODE.no-product-mocks: no proofs.toml [layout] covers src/a.py; declare "
        "test_file_patterns, support_dir, fixtures_dir in the root proofs.toml [layout]"
    ]
    (tmp_path / "proofs.toml").write_text('[layout]\nsupport_dir = "s"\n', encoding="utf-8")
    problem = _findings(checker, tmp_path, "src/a.py", "x = 1\n")[0]
    assert "lacks test_file_patterns, fixtures_dir" in problem


def test_nearest_proofs_file_sets_the_layout_root(checker: ModuleType, repo: Path) -> None:
    nested = repo / "fixtures" / "case"
    nested.mkdir(parents=True)
    (nested / "proofs.toml").write_text(_LAYOUT, encoding="utf-8")
    text = "from unittest import mock\n"
    assert _findings(checker, repo, "fixtures/case/tests/test_a.py", text) == []
    assert len(_findings(checker, repo, "fixtures/case/src/a.py", text)) == 1


def test_added_lines_reads_unified_zero_context_hunks(checker: ModuleType) -> None:
    diff = (
        "+++ b/a.py\n@@ -1,0 +2,3 @@\n+x\n@@ -9 +12 @@\n+y\n@@ -4,2 +14,0 @@\n"
        "+++ /dev/null\n@@ -1 +0,0 @@\n"
    )
    assert checker.added_lines(diff) == {"a.py": {2, 3, 4, 12}}


def test_glob_patterns_match_path_segments(checker: ModuleType) -> None:
    pattern = checker.glob_regex("dashboard/**/*.test.ts")
    assert pattern.fullmatch("dashboard/a.test.ts")
    assert pattern.fullmatch("dashboard/src/views/a.test.ts")
    assert not pattern.fullmatch("dashboard/src/a.ts")
    assert not checker.glob_regex("tests/test_*.py").fullmatch("tests/sub/test_a.py")
