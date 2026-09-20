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

SCRIPT = ROOT / "scripts" / "release_images.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"

ci = load(ROOT / "scripts" / "check_image_updates.py")
COMPOSE = ci.COMPOSE

LINE = re.compile(r"^\| `(?P<имя>[^`]+)` \| `(?P<тег>[^`]+)` \|$")


def build(*paths: str) -> str:
    rendered = subprocess.run(
        ["sh", str(SCRIPT), *paths],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert rendered.returncode == 0, f"скрипт упал: {rendered.stderr}"
    return rendered.stdout


def images(output: str) -> set[str]:
    """Строки таблицы обратно в ссылки вида репозиторий:тег."""
    found = set()
    for line in output.splitlines()[2:]:
        matched = LINE.match(line)
        assert matched, f"строка таблицы не разобралась: {line!r}"
        found.add(f"{matched['имя']}:{matched['тег']}")
    return found


def test_table_matches_the_pyyaml_parse() -> None:
    """Два разбора — на awk и на pyyaml — видят один и тот же список."""
    via_yaml = set()
    for path_str in COMPOSE:
        via_yaml.update(ci.images(path_str).values())
    assert images(build(*COMPOSE)) == via_yaml


def test_table_header_is_present() -> None:
    lines = build(*COMPOSE).splitlines()
    assert lines[0] == "| Образ | Версия |"
    assert lines[1] == "|---|---|"
    assert len(lines) > 2, "таблица без единого образа — описание выпуска будет пустым"


def test_word_image_in_a_comment_is_not_an_image(tmp_path) -> None:
    """Регрессия выпуска 1.1.0: в таблицу попала запятая из комментария."""
    path = tmp_path / "compose.yaml"
    path.write_text(
        "services:\n"
        "  app:\n"
        "    # он ищет поля image:, а здесь образ спрятан в переменной\n"
        "    #image: example.org/ghost:1.0.0\n"
        "    image: example.org/app:1.2.3\n",
        encoding="utf-8",
    )
    assert images(build(str(path))) == {"example.org/app:1.2.3"}


def test_mods_from_environment_reach_the_table(tmp_path) -> None:
    """DOCKER_MODS скачивается при каждом старте — в составе выпуска он нужен."""
    path = tmp_path / "compose.yaml"
    path.write_text(
        "services:\n"
        "  app:\n"
        "    image: example.org/app:1.2.3\n"
        "    environment:\n"
        "      - DOCKER_MODS=example.org/first:1.0.0|example.org/second:2.0.0\n"
        "      - UNIVERSAL_MODS=example.org/third:3.0.0\n"
        "      - NOT_A_MOD=example.org/nope:4.0.0\n",
        encoding="utf-8",
    )
    assert images(build(str(path))) == {
        "example.org/app:1.2.3",
        "example.org/first:1.0.0",
        "example.org/second:2.0.0",
        "example.org/third:3.0.0",
    }


def test_trailing_comment_does_not_stick_to_the_tag(tmp_path) -> None:
    path = tmp_path / "compose.yaml"
    path.write_text(
        "services:\n  app:\n    image: example.org/app:1.2.3  # см. выпуски\n",
        encoding="utf-8",
    )
    assert images(build(str(path))) == {"example.org/app:1.2.3"}


def test_image_without_tag_stops_the_release(tmp_path) -> None:
    """Молча выпустить «версию latest» нельзя: пусть падает здесь."""
    path = tmp_path / "compose.yaml"
    path.write_text("services:\n  app:\n    image: example.org/app\n", encoding="utf-8")
    rendered = subprocess.run(
        ["sh", str(SCRIPT), str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert rendered.returncode != 0
    assert "нет тега" in rendered.stderr


def test_without_arguments_the_script_makes_no_empty_table() -> None:
    rendered = subprocess.run(
        ["sh", str(SCRIPT)], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert rendered.returncode == 2


def test_release_builds_the_table_with_this_script() -> None:
    """Однострочник в workflow разошёлся с тестами один раз — хватит."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/release_images.sh" in text, (
        "описание выпуска собирается мимо scripts/release_images.sh — "
        "значит, разбор снова написан дважды и снова может разойтись"
    )
    for path_str in COMPOSE:
        assert path_str in text, f"{path_str} не передан скрипту — его образы не попадут в выпуск"
