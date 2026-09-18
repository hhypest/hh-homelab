"""
Проверки таблицы образов в описании выпуска.

Описание релиза собирается из compose-файлов, а не пишется руками —
чтобы оно физически не могло разойтись с тем, что закреплено
в конфигурации. Но собирается оно в задаче, которая ничего не ставит
из сети, то есть без pyyaml, — а значит, вторым разбором, отдельным
от scripts/check_image_updates.py.

Второй разбор уже один раз разошёлся с первым: в выпуске 1.1.0
в таблицу попала строка «, | ,» — прежний grep принял за образ запятую
из комментария «он ищет поля image:, а здесь». Поэтому здесь два разбора
сверяются между собой на настоящих compose-файлах, а отдельные тесты
держат случаи, на которых легко ошибиться снова.
"""

from __future__ import annotations

import re
import subprocess

from conftest import ROOT, load

СКРИПТ = ROOT / "scripts" / "release_images.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"

ci = load(ROOT / "scripts" / "check_image_updates.py")
COMPOSE = ci.COMPOSE

СТРОКА = re.compile(r"^\| `(?P<имя>[^`]+)` \| `(?P<тег>[^`]+)` \|$")


def собрать(*файлы: str) -> str:
    готово = subprocess.run(
        ["sh", str(СКРИПТ), *файлы],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert готово.returncode == 0, f"скрипт упал: {готово.stderr}"
    return готово.stdout


def образы(вывод: str) -> set[str]:
    """Строки таблицы обратно в ссылки вида репозиторий:тег."""
    найдено = set()
    for строка in вывод.splitlines()[2:]:
        совпало = СТРОКА.match(строка)
        assert совпало, f"строка таблицы не разобралась: {строка!r}"
        найдено.add(f"{совпало['имя']}:{совпало['тег']}")
    return найдено


def test_таблица_совпадает_с_разбором_через_pyyaml() -> None:
    """Два разбора — на awk и на pyyaml — видят один и тот же список."""
    через_yaml = set()
    for путь in COMPOSE:
        через_yaml.update(ci.images(путь).values())
    assert образы(собрать(*COMPOSE)) == через_yaml


def test_заголовок_таблицы_на_месте() -> None:
    строки = собрать(*COMPOSE).splitlines()
    assert строки[0] == "| Образ | Версия |"
    assert строки[1] == "|---|---|"
    assert len(строки) > 2, "таблица без единого образа — описание выпуска будет пустым"


def test_слово_image_в_комментарии_не_образ(tmp_path) -> None:
    """Регрессия выпуска 1.1.0: в таблицу попала запятая из комментария."""
    файл = tmp_path / "compose.yaml"
    файл.write_text(
        "services:\n"
        "  app:\n"
        "    # он ищет поля image:, а здесь образ спрятан в переменной\n"
        "    #image: example.org/ghost:1.0.0\n"
        "    image: example.org/app:1.2.3\n",
        encoding="utf-8",
    )
    assert образы(собрать(str(файл))) == {"example.org/app:1.2.3"}


def test_моды_из_окружения_попадают_в_таблицу(tmp_path) -> None:
    """DOCKER_MODS скачивается при каждом старте — в составе выпуска он нужен."""
    файл = tmp_path / "compose.yaml"
    файл.write_text(
        "services:\n"
        "  app:\n"
        "    image: example.org/app:1.2.3\n"
        "    environment:\n"
        "      - DOCKER_MODS=example.org/first:1.0.0|example.org/second:2.0.0\n"
        "      - UNIVERSAL_MODS=example.org/third:3.0.0\n"
        "      - NOT_A_MOD=example.org/nope:4.0.0\n",
        encoding="utf-8",
    )
    assert образы(собрать(str(файл))) == {
        "example.org/app:1.2.3",
        "example.org/first:1.0.0",
        "example.org/second:2.0.0",
        "example.org/third:3.0.0",
    }


def test_хвостовой_комментарий_не_прилипает_к_тегу(tmp_path) -> None:
    файл = tmp_path / "compose.yaml"
    файл.write_text(
        "services:\n  app:\n    image: example.org/app:1.2.3  # см. выпуски\n",
        encoding="utf-8",
    )
    assert образы(собрать(str(файл))) == {"example.org/app:1.2.3"}


def test_образ_без_тега_останавливает_выпуск(tmp_path) -> None:
    """Молча выпустить «версию latest» нельзя: пусть падает здесь."""
    файл = tmp_path / "compose.yaml"
    файл.write_text("services:\n  app:\n    image: example.org/app\n", encoding="utf-8")
    готово = subprocess.run(
        ["sh", str(СКРИПТ), str(файл)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert готово.returncode != 0
    assert "нет тега" in готово.stderr


def test_без_аргументов_скрипт_не_делает_пустую_таблицу() -> None:
    готово = subprocess.run(
        ["sh", str(СКРИПТ)], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert готово.returncode == 2


def test_выпуск_собирает_таблицу_этим_скриптом() -> None:
    """Однострочник в workflow разошёлся с тестами один раз — хватит."""
    текст = WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/release_images.sh" in текст, (
        "описание выпуска собирается мимо scripts/release_images.sh — "
        "значит, разбор снова написан дважды и снова может разойтись"
    )
    for путь in COMPOSE:
        assert путь in текст, f"{путь} не передан скрипту — его образы не попадут в выпуск"
