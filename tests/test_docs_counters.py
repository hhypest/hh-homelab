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

import yaml
from conftest import ROOT

DOCS = sorted((ROOT / "docs").glob("*.html"))


def badges(caption: str) -> list[tuple[pathlib.Path, int]]:
    """Все плашки с такой подписью и числа в них."""
    template = re.compile(r'<span class="fact">' + caption + r"\s*<b>(\d+)</b></span>")
    found = []
    for path in DOCS:
        for value in template.findall(path.read_text(encoding="utf-8")):
            found.append((path, int(value)))
    return found


def expect_equal(caption: str, expected_value: int) -> None:
    found = badges(caption)
    assert found, f"ни на одной странице нет плашки «{caption}» — проверка стала пустой"
    for path, value in found:
        assert value == expected_value, (
            f"{path.relative_to(ROOT)}: в плашке «{caption}» стоит {value}, "
            f"а на самом деле {expected_value}"
        )


def test_test_count_in_headers():
    """
    Число берём у самого pytest: --collect-only разворачивает parametrize,
    но ничего не запускает, поэтому рекурсии здесь нет.

    addopts из pytest.ini гасим: там уже стоит -q, и вторая -q переключает
    вывод на пофайловую сводку вместо списка тестов.
    """
    output = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q", "-o", "addopts="],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert output.returncode == 0, f"pytest не смог собрать тесты:\n{output.stdout}{output.stderr}"
    collected = sum(1 for line in output.stdout.splitlines() if "::" in line)
    assert collected > 0, f"не разобрали вывод pytest:\n{output.stdout}"
    expect_equal("тестов", collected)


def test_doc_page_count_in_headers():
    expect_equal("страниц документации", len(DOCS))


def test_tracked_file_count_in_headers():
    """
    «Под версией» — это ровно то, что показывает git ls-files: тот же
    список, по которому ходят check_files.py и проверка обезличивания.
    """
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert tracked.returncode == 0, "git ls-files не отработал"
    expect_equal("файлов под версией", len(tracked.stdout.splitlines()))


def test_test_count_in_readme():
    """
    Та же цифра стоит в README, в таблице задач CI, но не плашкой, —
    поэтому сверяется отдельным шаблоном, а не общей проверкой выше.
    """
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    found = re.findall(r"линтер и (\d+) тест[аов]* на Python", text)
    assert found, "в README пропала строка про число тестов — проверка стала пустой"
    output = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q", "-o", "addopts="],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    collected = sum(1 for line in output.stdout.splitlines() if "::" in line)
    for value in found:
        assert int(value) == collected, (
            f"README обещает {value} тестов, а pytest собирает {collected}"
        )


# -----------------------------------------------------------------------------
#  Числительные прописью: «пять автоматизаций» в таблице файлов
# -----------------------------------------------------------------------------
#
# Таблица «что внутри каждого файла» в чек-листе Home Assistant называет,
# сколько в пакете автоматизаций. Число написано словом, и потому не
# сверялось ничем: пакет docker.yaml получил пятую автоматизацию
# (перезапуски контейнера), а строка таблицы ещё месяц обещала четыре.
# Такое расхождение не видно при чтении — фраза выглядит одинаково
# правдоподобно с любым числительным.

NUMERALS = {
    "одна": 1, "две": 2, "три": 3, "четыре": 4, "пять": 5,
    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
}


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_multi_constructor("!", lambda loader, suffix, node: None)


def automations_in_package(name: str) -> int:
    path = ROOT / "homeassistant" / "config" / "packages" / name
    data = yaml.load(path.read_text(encoding="utf-8"), _Loader) or {}
    return len(data.get("automation") or [])


def test_automation_count_in_file_table():
    """
    Проверяются только те строки, где число названо: пакет, о котором
    в таблице сказано «пять автоматизаций», обязан иметь ровно пять.
    """
    text = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    cells = re.findall(
        r"<code>packages/(\w+\.yaml)</code>(.*?)</tr>", text, re.S,
    )
    assert cells, "в чек-листе пропала таблица пакетов — проверка стала пустой"
    checked = 0
    for name, description in cells:
        word = re.search(
            r"(" + "|".join(NUMERALS) + r")\s+автоматизаци", description,
        )
        if not word:
            continue
        checked += 1
        promised = NUMERALS[word.group(1)]
        actual = automations_in_package(name)
        assert promised == actual, (
            f"для {name} таблица обещает {word.group(1)} автоматизаций "
            f"({promised}), а в пакете их {actual}"
        )
    assert checked >= 3, (
        f"числительные нашлись только в {checked} строках — проверка почти пустая"
    )
