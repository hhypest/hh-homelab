"""
Цифры в шапках страниц: тесты, страницы, файлы.

Шапка обзора и истории версий начинается с плашек «тестов 146»,
«страниц документации 8», «файлов под версией 71». Ни одно из этих чисел
никогда не сверялось — и все три разъехались с репозиторием: тестов стало
234, страниц 9, файлов 79. Заметить это чтением нельзя: плашка выглядит
одинаково правдоподобно с любым числом.

Счётчик шагов в README сверяется давно (validate_docs.check_declared_step_counts),
и ровно поэтому он не врёт. Здесь та же мысль, но числа берутся не со
страницы, а из самого репозитория: сколько тестов собирает pytest, сколько
файлов в docs, сколько файлов под версией.

Почему в тестах, а не в validate_docs.py: посчитать тесты статически нельзя
— половина из них разворачивается из parametrize. Спросить об этом можно
только сам pytest.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

from conftest import ROOT

DOCS = sorted((ROOT / "docs").glob("*.html"))


def плашки(подпись: str) -> list[tuple[pathlib.Path, int]]:
    """Все плашки с такой подписью и числа в них."""
    шаблон = re.compile(r'<span class="fact">' + подпись + r"\s*<b>(\d+)</b></span>")
    найдено = []
    for path in DOCS:
        for значение in шаблон.findall(path.read_text(encoding="utf-8")):
            найдено.append((path, int(значение)))
    return найдено


def сверить(подпись: str, ожидается: int) -> None:
    найдено = плашки(подпись)
    assert найдено, f"ни на одной странице нет плашки «{подпись}» — проверка стала пустой"
    for path, значение in найдено:
        assert значение == ожидается, (
            f"{path.relative_to(ROOT)}: в плашке «{подпись}» стоит {значение}, "
            f"а на самом деле {ожидается}"
        )


def test_число_тестов_в_шапках():
    """
    Число берём у самого pytest: --collect-only разворачивает parametrize,
    но ничего не запускает, поэтому рекурсии здесь нет.

    addopts из pytest.ini гасим: там уже стоит -q, и вторая -q переключает
    вывод на пофайловую сводку вместо списка тестов.
    """
    вывод = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q", "-o", "addopts="],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert вывод.returncode == 0, f"pytest не смог собрать тесты:\n{вывод.stdout}{вывод.stderr}"
    собрано = sum(1 for line in вывод.stdout.splitlines() if "::" in line)
    assert собрано > 0, f"не разобрали вывод pytest:\n{вывод.stdout}"
    сверить("тестов", собрано)


def test_число_страниц_документации_в_шапках():
    сверить("страниц документации", len(DOCS))


def test_число_файлов_под_версией_в_шапках():
    """
    «Под версией» — это ровно то, что показывает git ls-files: тот же
    список, по которому ходят check_files.py и проверка обезличивания.
    """
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert tracked.returncode == 0, "git ls-files не отработал"
    сверить("файлов под версией", len(tracked.stdout.splitlines()))


def test_число_тестов_в_readme():
    """
    Та же цифра стоит в README, в таблице задач CI, но не плашкой, —
    поэтому сверяется отдельным шаблоном, а не общей проверкой выше.
    """
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    найдено = re.findall(r"линтер и (\d+) тест[аов]* на Python", text)
    assert найдено, "в README пропала строка про число тестов — проверка стала пустой"
    вывод = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q", "-o", "addopts="],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    собрано = sum(1 for line in вывод.stdout.splitlines() if "::" in line)
    for значение in найдено:
        assert int(значение) == собрано, (
            f"README обещает {значение} тестов, а pytest собирает {собрано}"
        )
