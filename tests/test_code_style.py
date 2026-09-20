"""
Единый стандарт кода: имена — латиницей, русский — в текстах.

До этого правила в репозитории было 416 идентификаторов кириллицей:
имена функций, параметров, локальных переменных, констант. Читались они
хорошо, но цена оказалась выше пользы. Имя функции попадает в трассировку,
в отчёт pytest, в вывод ruff, в diff, в поиск по коду и в сообщение
об ошибке — то есть в места, где кодировка, шрифт или чужой инструмент
не обязаны справляться с кириллицей. Плюс гомоглифы: «с» и «c» в имени
переменной выглядят одинаково, а это разные имена.

Договорённость простая: имена процедур, функций, переменных и модулей —
только латиницей. Русский язык остаётся там, где он и был полезен:
в документации, комментариях, docstring и текстах сообщений.

Запрет держат два правила ruff — PLC2401 и PLC2403. Раньше в ruff.toml
стояло послабление ровно обратного смысла («в тестах имена на русском
осознанно»), и оно не значило ничего: правила N, которые оно отключало,
в select не входили. Поэтому здесь три проверки, а не одна: правило
выбрано, правило умеет провалиться, и в репозитории нет нарушений.
"""

from __future__ import annotations

import ast
import subprocess
import sys

import pytest
from conftest import ROOT

RUFF_CONFIG = ROOT / "ruff.toml"
LATIN_RULES = ("PLC2401", "PLC2403")


def tracked_python_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=False,
    )
    return [line for line in result.stdout.splitlines() if line]


def defined_names(tree: ast.AST):
    """Имена, которые файл вводит сам: их и обязывает правило."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            yield node.name
            continue
        if isinstance(node, ast.arg):
            yield node.arg
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            yield node.id
            continue
        if isinstance(node, ast.alias):
            yield node.asname or node.name.split(".")[0]
            continue
        if isinstance(node, ast.ExceptHandler) and node.name:
            yield node.name
        if isinstance(node, ast.Global | ast.Nonlocal):
            yield from node.names


FILES = tracked_python_files()


def test_repository_has_python_files() -> None:
    """Без этого обход молча проверял бы пустой список."""
    assert len(FILES) > 20, f"git ls-files отдал {len(FILES)} файлов — обход ничего не проверяет"


@pytest.mark.parametrize("path", FILES)
def test_every_defined_name_is_latin(path: str) -> None:
    source = (ROOT / path).read_text(encoding="utf-8")
    foreign = sorted({name for name in defined_names(ast.parse(source)) if not name.isascii()})
    assert not foreign, (
        f"{path}: имена не латиницей — {foreign}. Русский текст уместен "
        f"в комментарии, docstring и сообщении, но не в имени."
    )


@pytest.mark.parametrize("rule", LATIN_RULES)
def test_ruff_selects_the_latin_rule(rule: str) -> None:
    """Правило должно стоять в select, иначе запрет существует только на словах."""
    config = RUFF_CONFIG.read_text(encoding="utf-8")
    select = config.split("select = [", 1)[1].split("]", 1)[0]
    assert f'"{rule}"' in select, f"{rule} пропал из select в ruff.toml — запрет перестал работать"


def test_the_rule_actually_rejects_a_russian_name() -> None:
    """
    Проверка, которая не может провалиться, хуже её отсутствия: сам факт
    строки в select ничего не доказывает, пока ruff не отверг такой код.
    """
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--config", str(RUFF_CONFIG),
         "--stdin-filename", "tests/test_probe.py", "-"],
        cwd=ROOT, input="def проверка() -> None:\n    return None\n",
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0, "ruff принял функцию с русским именем — запрет не работает"
    assert "PLC2401" in result.stdout, f"отказ пришёл не от того правила: {result.stdout!r}"


def test_russian_survived_in_texts() -> None:
    """
    Обратная сторона правила: имена латиницей не означают код без русского.
    Если кириллица исчезнет и из комментариев с сообщениями, документация
    внутри кода потеряется — а она здесь и есть объяснение, зачем проверка.
    """
    with_russian = [
        path for path in FILES
        if any(char.isalpha() and not char.isascii()
               for char in (ROOT / path).read_text(encoding="utf-8"))
    ]
    assert len(with_russian) > len(FILES) // 2, (
        f"русский текст остался лишь в {len(with_russian)} файлах из {len(FILES)} — "
        f"похоже, вместе с именами переписали комментарии и сообщения"
    )


def test_ci_runs_ruff() -> None:
    workflow = (ROOT / ".github" / "workflows" / "validate.yml").read_text(encoding="utf-8")
    assert "ruff check ." in workflow, "ruff пропал из CI — коммит с русским именем пройдёт молча"
