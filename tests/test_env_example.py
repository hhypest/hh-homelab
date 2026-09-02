"""
Проверки соответствия .env.example и compose-файлов.

В compose.yaml нет значений по умолчанию: каждая переменная объявлена как
${VAR:?...} и обязана прийти из .env. Это делает .env.example единственным
источником правды о том, что нужно задать, — и одновременно тем файлом,
про который проще всего забыть, добавляя переменную в compose.

Расхождение проявляется поздно и на чужой машине: человек копирует образец,
запускает docker compose up и получает падение на переменной, о которой
образец не сказал ни слова. Здесь это ловится сразу.
"""

from __future__ import annotations

import re

import pytest
from conftest import ROOT

PROJECTS = ["media", "homeassistant"]

# ${VAR}, ${VAR:?сообщение}, ${VAR:-умолчание} — имя нам нужно во всех формах.
VARIABLE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)[:?\-][^}]*\}|\$\{([A-Z_][A-Z0-9_]*)\}")

# Переменные самого Compose: их подставляет он сам, в .env им не место.
COMPOSE_OWN = {"COMPOSE_PROJECT_NAME", "COMPOSE_FILE", "PWD"}


def compose_variables(project: str) -> set[str]:
    text = (ROOT / project / "compose.yaml").read_text(encoding="utf-8")
    found = {m.group(1) or m.group(2) for m in VARIABLE.finditer(text)}
    return found - COMPOSE_OWN


def example_variables(project: str) -> set[str]:
    text = (ROOT / project / ".env.example").read_text(encoding="utf-8")
    return {
        line.split("=", 1)[0].strip()
        for line in text.splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }


@pytest.mark.parametrize("project", PROJECTS)
def test_example_covers_every_variable(project: str) -> None:
    """Каждая ${...} из compose.yaml должна быть в образце."""
    missing = compose_variables(project) - example_variables(project)
    assert not missing, (
        f"{project}/.env.example не описывает: {', '.join(sorted(missing))}. "
        f"Человек скопирует образец и получит падение на первом же запуске."
    )


@pytest.mark.parametrize("project", PROJECTS)
def test_example_has_nothing_extra(project: str) -> None:
    """И наоборот: переменная в образце, которую никто не читает, — мусор."""
    extra = example_variables(project) - compose_variables(project)
    assert not extra, (
        f"{project}/.env.example описывает лишнее: {', '.join(sorted(extra))}. "
        f"В compose.yaml эти переменные не используются."
    )


@pytest.mark.parametrize("project", PROJECTS)
def test_every_variable_is_required_somewhere(project: str) -> None:
    """
    Смысл затеи в том, что забытый .env роняет запуск, а не подставляет
    пустые строки. Для этого у каждой переменной должно быть хотя бы одно
    вхождение в форме ${VAR:?...}. Голое ${VAR} везде — и Compose вернёт
    код 0 со стеком без опубликованных портов.
    """
    text = (ROOT / project / "compose.yaml").read_text(encoding="utf-8")
    for name in sorted(compose_variables(project)):
        assert re.search(rf"\$\{{{re.escape(name)}:\?", text), (
            f"{project}/compose.yaml: ${{{name}}} нигде не помечена как обязательная — "
            f"забытый .env подставит сюда пустую строку молча"
        )


@pytest.mark.parametrize("project", PROJECTS)
def test_no_default_values_left(project: str) -> None:
    """
    Форма ${VAR:-умолчание} возвращает то, от чего мы ушли: чужие настройки,
    подставленные молча. В публичном репозитории это осечка на своей машине
    и неверное поведение на всех остальных.
    """
    text = (ROOT / project / "compose.yaml").read_text(encoding="utf-8")
    with_defaults = re.findall(r"\$\{([A-Z_][A-Z0-9_]*):-[^}]*\}", text)
    assert not with_defaults, (
        f"{project}/compose.yaml: значения по умолчанию у "
        f"{', '.join(sorted(set(with_defaults)))}"
    )


@pytest.mark.parametrize("project", PROJECTS)
def test_example_is_not_ignored_but_env_is(project: str) -> None:
    """
    .env.example обязан попадать в репозиторий, .env — не обязан никогда.
    Правило в .gitignore легко сломать одной строкой.
    """
    import subprocess

    ignored = subprocess.run(
        ["git", "check-ignore", f"{project}/.env", f"{project}/.env.example"],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout.split()
    assert f"{project}/.env" in ignored, f"{project}/.env не игнорируется — утечёт в историю"
    assert f"{project}/.env.example" not in ignored, f"{project}/.env.example игнорируется"


# --- связка с Home Assistant -------------------------------------------------
# HA опрашивает сервисы медиа-стека по адресу 127.0.0.1:<порт хоста>, а порты
# хоста теперь задаются в media/.env. Разъедутся — сенсор молча уйдёт
# в «не отвечает», Пачка пришлёт тревогу, и искать причину будут в контейнере,
# который на самом деле жив.

HA_PACKAGES = "homeassistant/config/packages"
LOOPBACK = re.compile(r"https?://127\.0\.0\.1:(\d+)")

# 8123 — порт самого Home Assistant, он в host-сети и в .env не выносится.
HA_OWN_PORTS = {"8123"}


def ports_from_examples() -> set[str]:
    ports = set()
    for project in PROJECTS:
        for name, value in (
            line.split("=", 1)
            for line in (ROOT / project / ".env.example").read_text(encoding="utf-8").splitlines()
            if "=" in line and not line.lstrip().startswith("#")
        ):
            if "PORT" in name and value.strip().isdigit():
                ports.add(value.strip())
    return ports


def test_home_assistant_polls_ports_that_exist_in_env() -> None:
    """
    Каждый порт, по которому Home Assistant стучится на петлю, должен быть
    в одном из .env.example. Поменяли порт в .env и забыли про monitoring.yaml —
    падает здесь, а не ложной тревогой в три часа ночи.
    """
    known = ports_from_examples() | HA_OWN_PORTS
    strays: list[str] = []
    for path in sorted((ROOT / HA_PACKAGES).glob("*.yaml")):
        for port in set(LOOPBACK.findall(path.read_text(encoding="utf-8"))):
            if port not in known:
                strays.append(f"{path.name}: 127.0.0.1:{port}")
    assert not strays, (
        "Home Assistant опрашивает порты, которых нет ни в одном .env.example: "
        + "; ".join(strays)
        + ". Либо порт сменили только в .env, либо только здесь."
    )
